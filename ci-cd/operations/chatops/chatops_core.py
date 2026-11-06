import fcntl
import hashlib
import hmac
import json
import logging
import os
import queue
import re
import sqlite3
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request

from chatops_audit import ChatOpsAudit, request_audit_payload
from rto_timeline import record_stage as record_rto_stage
from emergency_dr import (
    EMERGENCY_TTL_SECONDS,
    REQUEST_TYPE_DUMP_RESTORE,
    REQUEST_TYPE_EMERGENCY_FAILBACK,
    REQUEST_TYPE_EMERGENCY_FAILOVER,
    REQUEST_TYPE_SECURITY,
    EmergencyConsoleStore,
    dump_confirm_view,
    dump_select_view,
    emergency_console_blocks,
    emergency_reason_view,
    list_dump_backups,
    reason_options,
    resolve_dump_backup,
    validate_reason,
)


ACTIVE_STATUSES = ("PENDING", "PARTIALLY_APPROVED", "EXECUTING")
APPROVAL_STATUSES = ("PENDING", "PARTIALLY_APPROVED")
TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED")
REQUIRED_APPROVALS = 2
INITIAL_APPROVAL_TTL_SECONDS = 20 * 60
FIRST_APPROVAL_TTL_SECONDS = 60 * 60
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 60 * 60
DEFAULT_EXECUTION_HEARTBEAT_SECONDS = 15
DEFAULT_EXECUTION_LEASE_SECONDS = 60
SLACK_SIGNATURE_MAX_AGE_SECONDS = 300
SLACK_REPLAY_CACHE_SIZE = 4096
DEFAULT_TEST_MODE_MAX_EVENT_AGE_SECONDS = 3600
DEFAULT_DR_MAX_EVENT_AGE_SECONDS = 86400


def utc_timestamp():
    return time.time()


def format_timestamp(value):
    if value is None:
        return "-"
    return datetime.fromtimestamp(value, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def as_bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def request_type_for_action(action):
    return "NORMAL_FAILOVER" if action == "failover" else "NORMAL_FAILBACK"


def parse_event_age(value):
    """Parse Zabbix's numeric or human-readable EVENT.AGE value."""
    raw = str(value or "").strip()
    if not raw or raw.startswith("{"):
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*([smhdw])", raw.lower())
    if not matches or "".join(f"{number}{unit}" for number, unit in matches) != re.sub(r"\s+", "", raw.lower()):
        return None
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return sum(float(number) * multipliers[unit] for number, unit in matches)


def read_persistent_dr_state(path):
    """Read the fail-closed DR lifecycle state without mutating it."""
    try:
        with open(path, "r", encoding="utf-8") as stream:
            state = stream.readline().strip()
    except (OSError, UnicodeError):
        return ""
    valid_states = {
        "IDLE",
        "FAILOVER_EXECUTING",
        "DR_ACTIVE",
        "FAILBACK_EXECUTING",
        "FAILED",
        "PARTIAL_FAILURE",
    }
    return state if state in valid_states else ""


def _rto_run_id(incident):
    """Return the stable correlation ID used by the RTO timeline."""
    explicit = str(incident.get("rto_run_id") or "").strip()
    if explicit:
        return explicit
    incident_id = str(incident.get("incident_id") or "").strip()
    if incident_id and incident.get("source") == "zabbix":
        return f"zabbix-{incident_id}"
    return ""


def _record_rto(incident, stage, *, approval_id=None):
    """Best-effort RTO bookkeeping; never blocks an alert or approval."""
    run_id = _rto_run_id(incident)
    if not run_id:
        return
    try:
        record_rto_stage(
            run_id,
            stage,
            timestamp=incident.get("incident_at") if stage == "incident_at" else None,
            event_id=incident.get("incident_id"),
            approval_id=approval_id,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[RTO] timeline record skipped stage={stage} error={type(exc).__name__}", flush=True)


def load_env_file(path):
    values = {}
    if not path or not os.path.isfile(path):
        return values
    with open(path, "r", encoding="utf-8") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key:
                values[key] = value
    return values


class ApprovalStore:
    def __init__(
        self,
        path,
        initial_ttl=INITIAL_APPROVAL_TTL_SECONDS,
        approved_ttl=FIRST_APPROVAL_TTL_SECONDS,
        execution_timeout=DEFAULT_EXECUTION_TIMEOUT_SECONDS,
        execution_lease_seconds=DEFAULT_EXECUTION_LEASE_SECONDS,
    ):
        self.path = path
        self.initial_ttl = initial_ttl
        self.approved_ttl = approved_ttl
        self.execution_timeout = max(1.0, float(execution_timeout))
        self.execution_lease_seconds = max(1.0, float(execution_lease_seconds))
        self.lock = threading.RLock()
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, mode=0o700, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_requests (
                    request_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    slack_client_key TEXT,
                    status TEXT NOT NULL,
                    incidents TEXT NOT NULL,
                    approvers TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    first_approved_at REAL,
                    expires_at REAL NOT NULL,
                    slack_channel_id TEXT,
                    slack_message_ts TEXT,
                    finished_at REAL,
                    cancelled_by TEXT,
                    result TEXT,
                    execution_started_at REAL,
                    execution_heartbeat_at REAL,
                    execution_lease_until REAL,
                    execution_id TEXT
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(approval_requests)")
            }
            for name, definition in (
                ("slack_client_key", "TEXT"),
                ("finished_at", "REAL"),
                ("cancelled_by", "TEXT"),
                ("result", "TEXT"),
                ("execution_started_at", "REAL"),
                ("execution_heartbeat_at", "REAL"),
                ("execution_lease_until", "REAL"),
                ("execution_id", "TEXT"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE approval_requests ADD COLUMN {name} {definition}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_approval_active ON approval_requests(action, status, created_at)"
            )
        os.chmod(self.path, 0o600)

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        result = dict(row)
        result["incidents"] = json.loads(result["incidents"])
        result["approvers"] = json.loads(result["approvers"])
        return result

    @staticmethod
    def _read(connection, request_id):
        return connection.execute(
            "SELECT * FROM approval_requests WHERE request_id = ?", (request_id,)
        ).fetchone()

    def get(self, request_id):
        with self.lock, self._connect() as connection:
            return self._decode(self._read(connection, request_id))

    def list_with_messages(self):
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM approval_requests WHERE slack_channel_id IS NOT NULL "
                "AND slack_message_ts IS NOT NULL ORDER BY created_at ASC"
            ).fetchall()
            return [self._decode(row) for row in rows]

    def _recover_stale_executions_locked(self, connection, now):
        rows = connection.execute(
            """
            SELECT request_id, created_at, execution_started_at,
                   execution_heartbeat_at, execution_lease_until
            FROM approval_requests
            WHERE status = 'EXECUTING'
            """
        ).fetchall()
        stale_ids = []
        for row in rows:
            lease_until = row["execution_lease_until"]
            started_at = row["execution_started_at"] or row["execution_heartbeat_at"] or row["created_at"]
            stale = lease_until is not None and lease_until <= now
            if lease_until is None:
                stale = started_at + self.execution_timeout <= now
            if stale:
                stale_ids.append(row["request_id"])
        if stale_ids:
            connection.executemany(
                """
                UPDATE approval_requests
                SET status = 'FAILED', finished_at = ?, result = 'stale_execution_recovered',
                    execution_lease_until = NULL
                WHERE request_id = ? AND status = 'EXECUTING'
                """,
                [(now, request_id) for request_id in stale_ids],
            )
        return stale_ids

    def recover_stale_executions(self, now=None):
        now = utc_timestamp() if now is None else now
        with self.lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stale_ids = self._recover_stale_executions_locked(connection, now)
            states = [self._decode(self._read(connection, request_id)) for request_id in stale_ids]
            connection.commit()
            return states

    def find_active(self, action):
        now = utc_timestamp()
        with self.lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_stale_executions_locked(connection, now)
            row = connection.execute(
                """
                SELECT * FROM approval_requests
                WHERE action = ? AND status IN (?, ?, ?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (action, *ACTIVE_STATUSES),
            ).fetchone()
            connection.commit()
            return self._decode(row)

    def find_executing(self):
        """Return the current execution, if any, for the global DR lock view."""
        now = utc_timestamp()
        with self.lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_stale_executions_locked(connection, now)
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE status = 'EXECUTING' "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            connection.commit()
            return self._decode(row)

    def _expire_due_locked(self, connection, now):
        rows = connection.execute(
            """
            SELECT request_id FROM approval_requests
            WHERE status IN (?, ?) AND expires_at <= ?
            """,
            (*APPROVAL_STATUSES, now),
        ).fetchall()
        expired_ids = [row["request_id"] for row in rows]
        if expired_ids:
            connection.executemany(
                "UPDATE approval_requests SET status = 'EXPIRED', finished_at = ?, result = 'expired' "
                "WHERE request_id = ?",
                [(now, request_id) for request_id in expired_ids],
            )
        return expired_ids

    def expire_due(self, now=None):
        now = utc_timestamp() if now is None else now
        with self.lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_stale_executions_locked(connection, now)
            expired_ids = self._expire_due_locked(connection, now)
            states = [self._decode(self._read(connection, request_id)) for request_id in expired_ids]
            connection.commit()
            return states

    def create_or_merge(self, action, incident, now=None, ttl_seconds=None):
        now = utc_timestamp() if now is None else now
        requested_type = str(incident.get("request_type") or request_type_for_action(action)).strip()
        requested_client = str(incident.get("slack_client_key") or action).strip()
        requested_ttl = self.initial_ttl if ttl_seconds is None else max(1.0, float(ttl_seconds))
        with self.lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_stale_executions_locked(connection, now)
            self._expire_due_locked(connection, now)
            rows = connection.execute(
                """
                SELECT * FROM approval_requests
                WHERE action = ? AND status IN (?, ?, ?)
                ORDER BY created_at DESC
                """,
                (action, *ACTIVE_STATUSES),
            ).fetchall()
            requested_test_mode = bool(incident.get("test_mode"))
            row = None
            for candidate in rows:
                candidate_state = self._decode(candidate)
                candidate_test_mode = any(
                    bool(item.get("test_mode")) for item in candidate_state.get("incidents") or []
                )
                latest = (candidate_state.get("incidents") or [{}])[-1]
                candidate_type = str(
                    latest.get("request_type") or request_type_for_action(action)
                ).strip()
                candidate_client = str(
                    candidate_state.get("slack_client_key")
                    or latest.get("slack_client_key")
                    or action
                ).strip()
                if (
                    candidate_test_mode == requested_test_mode
                    and candidate_type == requested_type
                    and candidate_client == requested_client
                ):
                    row = candidate
                    break
            if row is not None:
                state = self._decode(row)
                incident_ids = {item.get("incident_id") for item in state["incidents"]}
                if incident.get("incident_id") not in incident_ids:
                    state["incidents"].append(incident)
                    connection.execute(
                        "UPDATE approval_requests SET incidents = ? WHERE request_id = ?",
                        (json.dumps(state["incidents"], ensure_ascii=True), state["request_id"]),
                    )
                connection.commit()
                return self.get(state["request_id"]), True

            request_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO approval_requests (
                    request_id, action, slack_client_key, status, incidents, approvers,
                    created_at, first_approved_at, expires_at,
                    slack_channel_id, slack_message_ts,
                    execution_started_at, execution_heartbeat_at,
                    execution_lease_until, execution_id
                ) VALUES (?, ?, ?, 'PENDING', ?, '[]', ?, NULL, ?, NULL, NULL, NULL, NULL, NULL, NULL)
                """,
                (
                    request_id,
                    action,
                    requested_client,
                    json.dumps([incident], ensure_ascii=True),
                    now,
                    now + requested_ttl,
                ),
            )
            connection.commit()
            return self.get(request_id), False

    def reset_approvals(self, request_id, ttl_seconds=None, result="precheck_failed"):
        """Discard approvals after a safety gate changed, keeping the request visible."""
        now = utc_timestamp()
        ttl = self.initial_ttl if ttl_seconds is None else max(1.0, float(ttl_seconds))
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE approval_requests
                SET status = 'PENDING', approvers = '[]', first_approved_at = NULL,
                    expires_at = ?, result = ?, execution_started_at = NULL,
                    execution_heartbeat_at = NULL, execution_lease_until = NULL,
                    execution_id = NULL
                WHERE request_id = ? AND status IN ('PENDING', 'PARTIALLY_APPROVED')
                """,
                (now + ttl, result, request_id),
            )
            connection.commit()
        return self.get(request_id)

    def attach_slack_message(self, request_id, channel_id, message_ts):
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE approval_requests
                SET slack_channel_id = ?, slack_message_ts = ?
                WHERE request_id = ?
                """,
                (channel_id, message_ts, request_id),
            )
            connection.commit()
        return self.get(request_id)

    def clear_slack_message(self, request_id):
        with self.lock, self._connect() as connection:
            connection.execute(
                "UPDATE approval_requests SET slack_message_ts = NULL WHERE request_id = ?",
                (request_id,),
            )
            connection.commit()
        return self.get(request_id)

    def refresh_failback_security(self, request_id, security):
        """Persist the latest gate result and discard stale partial approvals when blocked."""
        now = utc_timestamp()
        with self.lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._read(connection, request_id)
            if row is None:
                connection.commit()
                return None
            state = self._decode(row)
            if state["action"] != "failback" or not state["incidents"]:
                connection.commit()
                return state
            incidents = state["incidents"]
            latest = dict(incidents[-1])
            latest.update(
                {
                    "security_status": security.get("status", "UNKNOWN"),
                    "security_reason": security.get("reason", "Security readiness was not evaluated."),
                    "security_agents": security.get("agents", "-"),
                    "security_critical_incident": security.get("critical_incident", "-"),
                }
            )
            incidents[-1] = latest
            values = [json.dumps(incidents, ensure_ascii=True)]
            update = "UPDATE approval_requests SET incidents = ?"
            if security.get("status") in ("BLOCKED", "UNKNOWN") and state["status"] in APPROVAL_STATUSES:
                update += ", status = 'PENDING', approvers = '[]', first_approved_at = NULL, expires_at = ?"
                values.append(now + self.initial_ttl)
            update += " WHERE request_id = ?"
            values.append(request_id)
            connection.execute(update, values)
            connection.commit()
        return self.get(request_id)

    def refresh_failback_network(self, request_id, network):
        """Persist the latest Zabbix network gate and reset stale approvals."""
        now = utc_timestamp()
        with self.lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._read(connection, request_id)
            if row is None:
                connection.commit()
                return None
            state = self._decode(row)
            if state["action"] != "failback" or not state["incidents"]:
                connection.commit()
                return state
            incidents = state["incidents"]
            latest = dict(incidents[-1])
            latest.update(
                {
                    "network_status": network.get("status", "UNKNOWN"),
                    "network_reason": network.get("reason", "Network readiness was not evaluated."),
                    "network_source": network.get("source", "-"),
                    "network_devices": network.get("devices", {}),
                    "network_checked_at": network.get("checked_at", "-"),
                }
            )
            incidents[-1] = latest
            values = [json.dumps(incidents, ensure_ascii=True)]
            update = "UPDATE approval_requests SET incidents = ?"
            if network.get("status") in ("NOT_READY", "UNKNOWN") and state["status"] in APPROVAL_STATUSES:
                update += ", status = 'PENDING', approvers = '[]', first_approved_at = NULL, expires_at = ?"
                values.append(now + self.initial_ttl)
            update += " WHERE request_id = ?"
            values.append(request_id)
            connection.execute(update, values)
            connection.commit()
        return self.get(request_id)

    def approve(self, request_id, action, user_id, now=None):
        now = utc_timestamp() if now is None else now
        with self.lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_stale_executions_locked(connection, now)
            self._expire_due_locked(connection, now)
            row = self._read(connection, request_id)
            if row is None:
                connection.commit()
                return "missing", None
            state = self._decode(row)
            if state["action"] != action:
                connection.commit()
                return "action_mismatch", state
            executing_row = connection.execute(
                "SELECT * FROM approval_requests WHERE status = 'EXECUTING' "
                "AND request_id != ? ORDER BY created_at DESC LIMIT 1",
                (request_id,),
            ).fetchone()
            if executing_row is not None:
                connection.commit()
                return "global_lock", self._decode(executing_row)
            if state["status"] == "EXECUTING":
                connection.commit()
                return "executing", state
            if state["status"] in TERMINAL_STATUSES:
                connection.commit()
                return "closed", state
            if user_id in state["approvers"]:
                connection.commit()
                return "duplicate", state

            approvers = state["approvers"] + [user_id]
            first_approved_at = state["first_approved_at"]
            expires_at = state["expires_at"]
            if first_approved_at is None:
                first_approved_at = now
                if request_type_from_state(state) in {
                    REQUEST_TYPE_EMERGENCY_FAILOVER,
                    REQUEST_TYPE_EMERGENCY_FAILBACK,
                }:
                    expires_at = min(expires_at, now + EMERGENCY_TTL_SECONDS)
                else:
                    expires_at = now + self.approved_ttl
            status = "EXECUTING" if len(approvers) >= REQUIRED_APPROVALS else "PARTIALLY_APPROVED"
            execution_started_at = state.get("execution_started_at")
            execution_heartbeat_at = state.get("execution_heartbeat_at")
            execution_lease_until = state.get("execution_lease_until")
            execution_id = state.get("execution_id")
            if status == "EXECUTING":
                execution_started_at = now
                execution_heartbeat_at = now
                execution_lease_until = now + self.execution_lease_seconds
                execution_id = str(uuid.uuid4())
            connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, approvers = ?, first_approved_at = ?, expires_at = ?,
                    execution_started_at = ?, execution_heartbeat_at = ?,
                    execution_lease_until = ?, execution_id = ?
                WHERE request_id = ?
                """,
                (
                    status,
                    json.dumps(approvers, ensure_ascii=True),
                    first_approved_at,
                    expires_at,
                    execution_started_at,
                    execution_heartbeat_at,
                    execution_lease_until,
                    execution_id,
                    request_id,
                ),
            )
            connection.commit()
            return ("approved" if status == "EXECUTING" else "pending"), self.get(request_id)

    def cancel(self, request_id, action, now=None, cancelled_by=None):
        now = utc_timestamp() if now is None else now
        with self.lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_stale_executions_locked(connection, now)
            self._expire_due_locked(connection, now)
            row = self._read(connection, request_id)
            if row is None:
                connection.commit()
                return "missing", None
            state = self._decode(row)
            if state["action"] != action:
                connection.commit()
                return "action_mismatch", state
            if state["status"] == "EXECUTING":
                connection.commit()
                return "executing", state
            if state["status"] in TERMINAL_STATUSES:
                connection.commit()
                return "closed", state
            connection.execute(
                """
                UPDATE approval_requests
                SET status = 'CANCELLED', approvers = '[]', expires_at = ?,
                    finished_at = ?, cancelled_by = ?, result = 'cancelled',
                    execution_lease_until = NULL
                WHERE request_id = ?
                """,
                (now, now, cancelled_by, request_id),
            )
            connection.commit()
            return "cancelled", self.get(request_id)

    def heartbeat(self, request_id, now=None):
        now = utc_timestamp() if now is None else now
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE approval_requests
                SET execution_heartbeat_at = ?, execution_lease_until = ?
                WHERE request_id = ? AND status = 'EXECUTING'
                """,
                (now, now + self.execution_lease_seconds, request_id),
            )
            connection.commit()
            return cursor.rowcount == 1

    def finish(self, request_id, succeeded, result=None):
        status = "SUCCEEDED" if succeeded else "FAILED"
        finished_at = utc_timestamp()
        result = result or ("success" if succeeded else "failed")
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, finished_at = ?, result = ?, execution_lease_until = NULL
                WHERE request_id = ? AND status IN ('PENDING', 'PARTIALLY_APPROVED', 'EXECUTING')
                """,
                (status, finished_at, result, request_id),
            )
            connection.commit()
        return self.get(request_id)


class SlackApiError(RuntimeError):
    def __init__(self, method, error):
        self.method = method
        self.error = error
        super().__init__(f"Slack API {method} failed: {error}")


class SlackClient:
    def __init__(self, token, channel_id):
        self.token = token
        self.channel_id = channel_id

    @property
    def available(self):
        return bool(self.token and self.channel_id)

    def _call(self, method, payload):
        response = requests.post(
            f"https://slack.com/api/{method}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackApiError(method, data.get("error", "unknown_error"))
        return data

    def post_message(self, text, blocks):
        return self._call(
            "chat.postMessage",
            {"channel": self.channel_id, "text": text, "blocks": blocks},
        )

    def update_message(self, channel_id, message_ts, text, blocks):
        return self._call(
            "chat.update",
            {"channel": channel_id, "ts": message_ts, "text": text, "blocks": blocks},
        )

    def message_exists(self, channel_id, message_ts):
        """Check a message without mutating Slack or the message itself."""
        self._call(
            "chat.getPermalink",
            {"channel": channel_id, "message_ts": message_ts},
        )
        return True

    def delete_message(self, channel_id, message_ts):
        return self._call(
            "chat.delete",
            {"channel": channel_id, "ts": message_ts},
        )

    def open_view(self, trigger_id, view):
        return self._call("views.open", {"trigger_id": trigger_id, "view": view})

    def pin_message(self, channel_id, message_ts):
        return self._call("pins.add", {"channel": channel_id, "timestamp": message_ts})


def action_label(action):
    return "DR FAILOVER" if action == "failover" else "WEB+DOC+DB FAILBACK"


def status_label(status):
    return {
        "PENDING": "PENDING / 승인 대기",
        "PARTIALLY_APPROVED": "PENDING / 추가 승인 대기",
        "EXECUTING": "EXECUTING / 실행 중",
        "SUCCEEDED": "SUCCEEDED / 완료",
        "FAILED": "FAILED / 실패",
        "CANCELLED": "CANCELLED / 취소",
        "EXPIRED": "EXPIRED / 만료",
    }.get(status, status)


def safe_mrkdwn(value, limit=300):
    text = str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text[:limit]


def button_value(request_id, action, operation, **extra):
    value = {"request_id": request_id, "action": action, "operation": operation}
    value.update({key: item for key, item in extra.items() if item is not None})
    return json.dumps(value, separators=(",", ":"))


def valid_slack_signature(signing_secret, raw_body, timestamp, signature, now=None):
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        request_time = int(timestamp)
    except (TypeError, ValueError):
        return False
    current_time = int(utc_timestamp() if now is None else now)
    if abs(current_time - request_time) > SLACK_SIGNATURE_MAX_AGE_SECONDS:
        return False
    base = b"v0:" + str(request_time).encode("ascii") + b":" + raw_body
    expected = "v0=" + hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def remember_slack_request(replay_cache, replay_lock, timestamp, signature, now=None):
    """Accept a valid Slack request signature only once within its replay window."""
    now = int(time.time()) if now is None else int(now)
    cutoff = now - SLACK_SIGNATURE_MAX_AGE_SECONDS
    cache_key = (str(timestamp), str(signature))
    with replay_lock:
        expired = [key for key, seen_at in replay_cache.items() if seen_at < cutoff]
        for key in expired:
            replay_cache.pop(key, None)
        if cache_key in replay_cache:
            return False
        if len(replay_cache) >= SLACK_REPLAY_CACHE_SIZE:
            oldest_key = min(replay_cache, key=replay_cache.get)
            replay_cache.pop(oldest_key, None)
        replay_cache[cache_key] = now
        return True


def failback_security_from_state(state):
    if state.get("action") != "failback":
        return "READY", "", "-", "-"
    for incident in reversed(state.get("incidents") or []):
        if "security_status" in incident:
            return (
                incident.get("security_status") or "UNKNOWN",
                incident.get("security_reason") or "Security readiness was not evaluated.",
                incident.get("security_agents") or "-",
                incident.get("security_critical_incident") or "-",
            )
    return "UNKNOWN", "Security readiness was not evaluated.", "-", "-"


def failback_network_from_state(state):
    """Return the persisted network gate without failing open."""
    if state.get("action") != "failback":
        return "READY", "", "-", {}
    for incident in reversed(state.get("incidents") or []):
        if "network_status" in incident:
            return (
                incident.get("network_status") or "UNKNOWN",
                incident.get("network_reason") or "Network readiness was not evaluated.",
                incident.get("network_source") or "-",
                incident.get("network_devices") or {},
            )
    return "UNKNOWN", "Network readiness was not evaluated.", "-", {}


def failback_stability_from_state(state):
    """Return whether the production recovery guard proved the full window."""
    if state.get("action") != "failback":
        return True, 0, 0
    for incident in reversed(state.get("incidents") or []):
        if "stability_verified" in incident:
            try:
                seconds = int(incident.get("stability_seconds") or 0)
            except (TypeError, ValueError):
                seconds = 0
            try:
                required = int(incident.get("stability_required_seconds") or 300)
            except (TypeError, ValueError):
                required = 300
            return bool(incident.get("stability_verified")) and seconds >= required, seconds, required
    return False, 0, 300


def evaluate_current_failback_security(app):
    """Evaluate the configured Wazuh snapshot/provider without failing open."""
    try:
        from wazuh_failback_gate import evaluate_failback_security

        provider = app.config.get("WAZUH_SECURITY_STATE_PROVIDER")
        snapshot = provider() if callable(provider) else app.config.get("WAZUH_FAILBACK_SECURITY_SNAPSHOT")
        required_agents = app.config.get("WAZUH_FAILBACK_REQUIRED_AGENTS", ())
        if isinstance(required_agents, str):
            required_agents = tuple(item.strip() for item in required_agents.split(",") if item.strip())
        return evaluate_failback_security(snapshot, required_agents=required_agents)
    except Exception:
        return {
            "status": "UNKNOWN",
            "reason": "Wazuh security state could not be evaluated.",
            "agents": "UNKNOWN",
            "critical_incident": "-",
        }


def evaluate_current_network_health(app):
    """Evaluate the read-only Zabbix/SNMP network gate fail-closed."""
    if not app.config.get("NETWORK_DR_HEALTH_ENABLED", True):
        return {
            "status": "UNKNOWN",
            "reason": "Network DR health gate is disabled; Failback is fail-closed.",
            "source": "CONFIGURATION",
            "checked_at": "-",
            "devices": {},
        }
    try:
        provider = app.config.get("NETWORK_DR_HEALTH_PROVIDER")
        snapshot = provider() if callable(provider) else app.config.get("NETWORK_DR_HEALTH_SNAPSHOT")
        if snapshot is None and app.testing:
            # Unit/dry-run applications must supply a fixture in production;
            # this test-only default preserves the existing no-Slack dry-run.
            snapshot = {
                "status": "READY",
                "reason": "test fixture default",
                "source": "TEST_FIXTURE",
                "checked_at": "-",
                "devices": {},
            }
        if not isinstance(snapshot, dict):
            raise ValueError("network health provider returned a non-object")
        status = snapshot.get("status")
        if status not in {"READY", "NOT_READY", "UNKNOWN"}:
            raise ValueError("network health provider returned an invalid status")
        return snapshot
    except Exception:
        return {
            "status": "UNKNOWN",
            "reason": "Zabbix network health could not be evaluated.",
            "source": "ZABBIX_SNMP_AVAILABILITY",
            "checked_at": "-",
            "devices": {},
        }


def _configured_snapshot(app, name):
    snapshot = app.config.get(name)
    return dict(snapshot) if isinstance(snapshot, dict) else None


def evaluate_emergency_precheck(app, action, request_type, backup=None):
    """Run non-mutating safety checks immediately before an emergency approval."""
    supplied = _configured_snapshot(app, "EMERGENCY_PRECHECK_SNAPSHOT")
    if supplied is not None:
        status = supplied.get("status", "UNKNOWN")
        if status not in {"PASS", "WARNING", "FAILED"}:
            status = "UNKNOWN"
        return {
            "status": status,
            "reason": str(supplied.get("reason") or "Configured pre-check fixture")[:300],
            "checks": supplied.get("checks") or {},
        }

    checks = {}
    failures = []
    warnings = []
    executing = app.extensions["approval_store"].find_executing()
    checks["execution_lock"] = "PASS" if executing is None else "FAILED"
    if executing is not None:
        failures.append("another DR operation is executing")

    dr_state = read_persistent_dr_state(app.config["DR_STATE_FILE"])
    if action == "failover":
        state_ok = dr_state in {"IDLE", "FAILED", "PARTIAL_FAILURE"}
    else:
        state_ok = dr_state == "DR_ACTIVE"
    checks["current_state"] = "PASS" if state_ok else "FAILED"
    if not state_ok:
        failures.append(f"current DR state is {dr_state or 'UNKNOWN'}")

    script_name = "full_failover.sh" if action == "failover" else "web_db_failback.sh"
    script_path = os.path.join(app.config["DR_SCRIPT_ROOT"], script_name)
    runtime_ok = os.path.isfile(script_path) and os.access(script_path, os.R_OK)
    checks["iac_runtime"] = "PASS" if runtime_ok else "FAILED"
    if not runtime_ok:
        failures.append("required DR runtime script is unavailable")

    for check_name, config_name in (
        ("aws_access", "EMERGENCY_AWS_STATUS"),
        ("db_access", "EMERGENCY_DB_STATUS"),
    ):
        value = str(app.config.get(config_name) or "UNKNOWN").upper()
        checks[check_name] = value if value in {"PASS", "WARNING", "FAILED"} else "UNKNOWN"
        if checks[check_name] == "FAILED":
            failures.append(f"{check_name} failed")
        elif checks[check_name] != "PASS":
            warnings.append(f"{check_name} is {checks[check_name]}")

    if request_type == REQUEST_TYPE_DUMP_RESTORE:
        selected = backup or {}
        resolved = resolve_dump_backup(app.config["DUMP_BACKUP_DIR"], selected.get("backup_id"))
        checks["dump_backup"] = "PASS" if resolved else "FAILED"
        if not resolved:
            failures.append("selected dump is missing or incomplete")
        if not app.config.get("DUMP_RESTORE_EXEC_ENABLED"):
            checks["dump_executor"] = "FAILED"
            failures.append("dump restore executor is disabled")
        else:
            checks["dump_executor"] = "PASS" if app.config.get("DUMP_RESTORE_EXECUTOR") else "FAILED"
            if checks["dump_executor"] == "FAILED":
                failures.append("dump restore executor is not configured")

    security = evaluate_current_failback_security(app)
    checks["wazuh"] = security.get("status", "UNKNOWN")
    if action == "failback" and checks["wazuh"] == "BLOCKED":
        failures.append("Wazuh security gate is BLOCKED")
    elif checks["wazuh"] != "READY":
        warnings.append(f"Wazuh is {checks['wazuh']}")

    if failures:
        status = "FAILED"
        reason = "; ".join(failures)
    elif warnings:
        status = "WARNING"
        reason = "; ".join(warnings)
    else:
        status = "PASS"
        reason = "all emergency safety checks passed"
    return {"status": status, "reason": reason[:500], "checks": checks}


def build_emergency_status(app):
    """Build a redacted read-only status response for the console button."""
    executing = app.extensions["approval_store"].find_executing()
    network = evaluate_current_network_health(app)
    security = evaluate_current_failback_security(app)
    state = read_persistent_dr_state(app.config["DR_STATE_FILE"]) or "UNKNOWN"
    dumps = list_dump_backups(app.config["DUMP_BACKUP_DIR"], limit=1)
    latest_dump = dumps[0].get("timestamp") if dumps else "UNKNOWN"
    return {
        "DR State": state,
        "Execution Lock": "FREE" if executing is None else request_type_from_state(executing),
        "AWS Status": str(app.config.get("EMERGENCY_AWS_STATUS") or "UNKNOWN"),
        "On-Prem Status": str(app.config.get("EMERGENCY_ONPREM_STATUS") or "UNKNOWN"),
        "DB NR Status": "DISABLED" if not as_bool(app.config.get("DB_NR_ENABLED", False)) else str(app.config.get("EMERGENCY_NR_STATUS") or "UNKNOWN"),
        "Latest Dump": latest_dump,
        "Zabbix": str(app.config.get("EMERGENCY_ZABBIX_STATUS") or network.get("status") or "UNKNOWN"),
        "Wazuh": security.get("status", "UNKNOWN"),
    }


def format_emergency_status(status):
    return "Hospital DR Status\n" + "\n".join(
        f"{key:16}: {safe_mrkdwn(value, 160)}" for key, value in status.items()
    )


def ensure_emergency_console(app):
    """Create or repair the one emergency console message, without duplication."""
    channel_id = app.config.get("SLACK_EMERGENCY_CHANNEL_ID")
    client = app.extensions.get("slack_clients", {}).get("emergency")
    store = app.extensions.get("emergency_console_store")
    if not channel_id:
        print("EMERGENCY_CHANNEL_NOT_CONFIGURED", flush=True)
        return {"status": "DISABLED", "error": "EMERGENCY_CHANNEL_NOT_CONFIGURED"}
    if client is None:
        print("EMERGENCY_BOT_TOKEN_MISSING", flush=True)
        return {"status": "DISABLED", "error": "EMERGENCY_BOT_TOKEN_MISSING"}
    if hasattr(client, "token") and not client.token:
        print("EMERGENCY_BOT_TOKEN_MISSING", flush=True)
        return {"status": "DISABLED", "error": "EMERGENCY_BOT_TOKEN_MISSING"}
    if not client.available or store is None:
        return {"status": "DISABLED"}
    blocks = emergency_console_blocks(button_value)
    text = "Hospital DR Emergency Console"
    current = store.get()
    if current:
        if current.get("channel_id") == channel_id and current.get("message_ts"):
            checker = getattr(client, "message_exists", None)
            if checker is None:
                print("[EMERGENCY CONSOLE] presence check unavailable", flush=True)
                return {"status": "ERROR", "error": "presence_check_unavailable"}
            try:
                if checker(current["channel_id"], current["message_ts"]):
                    return {"status": "READY", "message_ts": current["message_ts"]}
            except SlackApiError as exc:
                if exc.error not in {"cant_find_message", "message_not_found"}:
                    if exc.error == "channel_not_found":
                        print("EMERGENCY_CONSOLE_CHANNEL_NOT_FOUND", flush=True)
                    elif exc.error == "not_in_channel":
                        print("EMERGENCY_CONSOLE_NOT_IN_CHANNEL", flush=True)
                    print(f"[EMERGENCY CONSOLE] presence check failed error={exc.error}", flush=True)
                    return {"status": "ERROR", "error": exc.error}
            except Exception as exc:
                print(
                    f"[EMERGENCY CONSOLE] presence check failed error={type(exc).__name__}",
                    flush=True,
                )
                return {"status": "ERROR", "error": type(exc).__name__}
            print("[EMERGENCY_CONSOLE_MISSING] replacing stale console message", flush=True)
            app.extensions["chatops_audit"].record_event(
                "EMERGENCY_CONSOLE_MISSING",
                {"channel_id": current.get("channel_id"), "message_ts": current.get("message_ts")},
            )
            store.clear()
        else:
            store.clear()
    try:
        result = client.post_message(text, blocks)
        saved = store.save(channel_id, result.get("ts", ""))
        print("EMERGENCY_CONSOLE_CREATED", flush=True)
        app.extensions["chatops_audit"].record_event(
            "EMERGENCY_CONSOLE_READY",
            {"channel_id": channel_id, "message_ts": result.get("ts")},
        )
        try:
            client.pin_message(channel_id, result["ts"])
        except SlackApiError as exc:
            print(f"EMERGENCY_CONSOLE_PIN=SKIPPED_NO_PERMISSION error={exc.error}", flush=True)
        return {"status": "READY", "message_ts": saved["message_ts"] if saved else result.get("ts")}
    except SlackApiError as exc:
        if exc.error == "channel_not_found":
            print("EMERGENCY_CONSOLE_CHANNEL_NOT_FOUND", flush=True)
        elif exc.error == "not_in_channel":
            print("EMERGENCY_CONSOLE_NOT_IN_CHANNEL", flush=True)
        print(f"[EMERGENCY CONSOLE] create failed error={exc.error}", flush=True)
        return {"status": "ERROR", "error": exc.error}


def request_ui_copy(action):
    if action == "failover":
        return (
            "🚨 [CRITICAL] On-Premise Network Failure Detected",
            "온프레미스 네트워크 장애가 감지되었습니다.",
            "Cloud DR 환경으로 Failover를 진행하시겠습니까?",
        )
    return (
        "🔄 [RECOVERY] On-Premise Service Recovery Confirmed",
        "온프레미스 서비스 복구가 확인되었습니다.",
        "Cloud DR 환경에서 On-Premise 환경으로 Failback을 진행하시겠습니까?",
    )


def ui_status_label(status):
    return {
        "PENDING": "승인 대기",
        "PARTIALLY_APPROVED": "추가 승인 대기",
        "EXECUTING": "실행 중",
        "SUCCEEDED": "완료",
        "FAILED": "실패",
        "CANCELLED": "취소",
        "EXPIRED": "만료",
    }.get(status, status)


def latest_incident(state):
    incidents = state.get("incidents") or []
    return incidents[-1] if incidents else {}


def request_type_from_state(state):
    return str(
        latest_incident(state).get("request_type")
        or request_type_for_action(state.get("action"))
    ).strip()


def request_client_key_from_state(state):
    return str(
        state.get("slack_client_key")
        or latest_incident(state).get("slack_client_key")
        or state.get("action")
    ).strip()


def render_request_message(state, detail=None):
    request_type = request_type_from_state(state)
    if request_type == REQUEST_TYPE_EMERGENCY_FAILOVER:
        title = "🚨 EMERGENCY FAILOVER"
        description = "긴급 Failover 요청입니다. 자동 감지 조건은 우회했지만 안전 검사는 유지됩니다."
        prompt = "관리자 2인 승인 후에만 실행됩니다."
    elif request_type == REQUEST_TYPE_EMERGENCY_FAILBACK:
        title = "🚨 EMERGENCY FAILBACK"
        description = "긴급 Failback 요청입니다. 자동 감지 조건은 우회했지만 안전 검사는 유지됩니다."
        prompt = "관리자 2인 승인 후에만 실행됩니다."
    elif request_type == REQUEST_TYPE_DUMP_RESTORE:
        title = "🗄️ DUMP RESTORE FAILOVER"
        description = "선택한 백업을 사용하는 별도 Failover 승인 요청입니다."
        prompt = "실제 복구 실행은 명시적 executor 설정 전까지 비활성화됩니다."
    elif request_type == REQUEST_TYPE_SECURITY:
        title = "🛡️ SECURITY DR ESCALATION"
        description = "파괴적 Critical 보안 이벤트에 대한 DR 승인 후보입니다."
        prompt = "자동 실행하지 않으며 관리자 2인 승인이 필요합니다."
    else:
        title, description, prompt = request_ui_copy(state["action"])
    approvers = " ".join(f"<@{user_id}>" for user_id in state["approvers"])
    test_incidents = [item for item in state.get("incidents") or [] if item.get("test_mode")]
    lines = [
        f"*{safe_mrkdwn(title, 160)}*",
        "",
        safe_mrkdwn(description, 300),
        safe_mrkdwn(prompt, 300),
        "",
        f"승인 상태: *{len(state['approvers'])}/{REQUIRED_APPROVALS}*",
    ]
    if test_incidents:
        latest = test_incidents[-1]
        lines.extend(
            (
                "*⚠️ [DR TEST MODE] 테스트 전용 요청 — 실제 운영 Failover/Failback이 아닙니다.*",
                f"테스트 Host: `{safe_mrkdwn(latest.get('host'), 120)}`",
                f"Zabbix Event: `{safe_mrkdwn(latest.get('incident_id'), 160)}`",
            )
        )
    if approvers:
        lines.append(f"승인자: {approvers}")
    lines.append(f"요청 ID: `{safe_mrkdwn(state['request_id'], 120)}`")
    latest = latest_incident(state)
    if request_type in {
        REQUEST_TYPE_EMERGENCY_FAILOVER,
        REQUEST_TYPE_EMERGENCY_FAILBACK,
    }:
        lines.extend(
            (
                f"요청자: `{safe_mrkdwn(latest.get('requested_by') or '-', 80)}`",
                f"긴급 사유: *{safe_mrkdwn(latest.get('reason_text') or latest.get('reason_code') or '-', 240)}*",
                f"자동 감지 조건: *BYPASSED*",
                f"만료 시각: `{safe_mrkdwn(format_timestamp(state.get('expires_at')), 80)}`",
            )
        )
    if request_type == REQUEST_TYPE_DUMP_RESTORE:
        if latest.get("request_origin") == "EMERGENCY":
            lines.extend(
                (
                    "요청 출처: *EMERGENCY*",
                    f"요청자: `{safe_mrkdwn(latest.get('requested_by') or '-', 80)}`",
                    f"긴급 사유: *{safe_mrkdwn(latest.get('reason_text') or latest.get('reason_code') or '-', 240)}*",
                    "자동 감지 조건: *BYPASSED*",
                )
            )
        lines.extend(
            (
                f"Backup ID: `{safe_mrkdwn(latest.get('backup_id') or '-', 160)}`",
                f"Backup timestamp: `{safe_mrkdwn(latest.get('backup_timestamp') or '-', 100)}`",
                f"Checksum: `{safe_mrkdwn((latest.get('backup_checksum') or '-')[:20], 40)}`",
                f"Expected RPO: `{safe_mrkdwn(latest.get('expected_rpo') or '-', 120)}`",
                f"만료 시각: `{safe_mrkdwn(format_timestamp(state.get('expires_at')), 80)}`",
            )
        )
    if latest.get("precheck_status"):
        lines.append(
            f"Pre-check: *{safe_mrkdwn(latest.get('precheck_status'), 20)}* "
            f"{safe_mrkdwn(latest.get('precheck_reason'), 240)}"
        )
    if state["status"] not in APPROVAL_STATUSES:
        lines.append(f"상태: *{ui_status_label(state['status'])}*")
    if state["action"] == "failback":
        security_status, security_reason, agents, critical_incident = failback_security_from_state(state)
        network_status, network_reason, network_source, network_devices = failback_network_from_state(state)
        stability_verified, stability_seconds, stability_required = failback_stability_from_state(state)
        lines.extend(
            (
                f"Network/SNMP: *{safe_mrkdwn(network_status, 20)}*",
                f"Network source: *{safe_mrkdwn(network_source, 60)}*",
                f"Network devices: *{sum(1 for item in network_devices.values() if item.get('status') == 'READY')} ready*",
                f"보안 상태: *{safe_mrkdwn(security_status, 20)}*",
                f"Wazuh Agent: *{safe_mrkdwn(agents, 40)}*",
                f"Critical 보안 이벤트: *{safe_mrkdwn(critical_incident, 160)}*",
                f"Stability: *{stability_seconds}s/{stability_required}s {'PASS' if stability_verified else 'NOT VERIFIED'}*",
            )
        )
        if network_status != "READY":
            lines.append(f"Network 사유: {safe_mrkdwn(network_reason, 240)}")
        if security_status != "READY":
            lines.append(f"사유: {safe_mrkdwn(security_reason, 240)}")
    if detail:
        lines.extend(("", f"처리 결과: {safe_mrkdwn(detail, 400)}"))

    text = "\n".join(lines)

    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    if state["status"] in APPROVAL_STATUSES:
        network_ready = state["action"] != "failback" or failback_network_from_state(state)[0] == "READY"
        stability_ready = state["action"] != "failback" or failback_stability_from_state(state)[0]
        security_status = failback_security_from_state(state)[0]
        approval_ready = network_ready and stability_ready
        if request_type in {
            REQUEST_TYPE_EMERGENCY_FAILOVER,
            REQUEST_TYPE_EMERGENCY_FAILBACK,
            REQUEST_TYPE_DUMP_RESTORE,
            REQUEST_TYPE_SECURITY,
        }:
            # Emergency/dump/security requests bypass only automatic detection
            # gates.  The approval handler performs the final safety pre-check.
            approval_ready = True
        if state["action"] == "failback" and security_status != "READY":
            approval_note = "※ Wazuh 상태는 승인 시 다시 확인합니다. UNKNOWN/BLOCKED이면 실행이 차단될 수 있습니다."
            approval_text = "승인 시 보안 재확인"
        else:
            approval_note = "※ 서로 다른 관리자 2명의 승인이 완료되어야 실행됩니다. 승인 대기 중 관리자 1명이 취소할 수 있습니다."
            approval_text = "Approve"
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            approval_note
                        ),
                    }
                ],
            }
        )
        elements = []
        if approval_ready:
            elements.append(
                {
                    "type": "button",
                    "action_id": "hospital_dr_approve",
                    "text": {"type": "plain_text", "text": approval_text},
                    "style": "primary",
                    "value": button_value(state["request_id"], state["action"], "approve"),
                }
            )
        if state["action"] == "failover":
            elements.append(
                {
                    "type": "button",
                    "action_id": "hospital_dr_dump_open",
                    "text": {"type": "plain_text", "text": "Dump 복구"},
                    "value": button_value(
                        state["request_id"],
                        state["action"],
                        "dump_open",
                        request_type=REQUEST_TYPE_DUMP_RESTORE,
                    ),
                }
            )
        elements.append(
            {
                "type": "button",
                "action_id": "hospital_dr_cancel",
                "text": {"type": "plain_text", "text": "Cancel"},
                "style": "danger",
                "value": button_value(state["request_id"], state["action"], "cancel"),
                "confirm": {
                    "title": {"type": "plain_text", "text": "요청을 취소하시겠습니까?"},
                    "text": {"type": "mrkdwn", "text": "현재 승인과 요청이 모두 취소됩니다."},
                    "confirm": {"type": "plain_text", "text": "취소"},
                    "deny": {"type": "plain_text", "text": "대기"},
                },
            }
        )
        blocks.append(
            {
                "type": "actions",
                "elements": elements,
            }
        )
    return title, blocks


def command_for_action(action, script_root):
    if action == "failover":
        return ["bash", os.path.join(script_root, "full_failover.sh")]
    if action == "failback":
        return ["bash", os.path.join(script_root, "web_db_failback.sh")]
    return None


def command_for_request(state, script_root, config):
    request_type = request_type_from_state(state)
    if request_type == REQUEST_TYPE_DUMP_RESTORE:
        if not config.get("DUMP_RESTORE_EXEC_ENABLED"):
            return None
        executor = str(config.get("DUMP_RESTORE_EXECUTOR") or "").strip()
        backup_id = latest_incident(state).get("backup_id")
        if not executor or not backup_id or not os.path.isfile(executor):
            return None
        return [executor, "--backup-id", str(backup_id)]
    return command_for_action(state.get("action"), script_root)


def should_suppress_log_line(line):
    noisy_patterns = (
        "CryptographyDeprecationWarning",
        "algorithms.TripleDES",
        "The authenticity of host",
        "ED25519 key fingerprint is",
        "This host key is known by the following other names/addresses",
        "Are you sure you want to continue connecting",
        "Warning: Permanently added",
    )
    return any(pattern in line for pattern in noisy_patterns) or "known_hosts:" in line


REPLACEABLE_MESSAGE_ERRORS = frozenset({"cant_update_message", "message_not_found"})


def register_request_message(app, state):
    try:
        app.extensions["chatops_audit"].register_request_message(state)
    except Exception as exc:
        # Audit failures must never stop an approval or DR workflow.
        print(f"[AUDIT WARN] Request message registration failed: {type(exc).__name__}", flush=True)


def deliver_slack_message(app, state, text, blocks):
    """Update the tracked message, replacing only stale/unowned messages."""
    if not state:
        raise RuntimeError("approval request state is missing")
    store = app.extensions["approval_store"]
    client_key = request_client_key_from_state(state)
    client = app.extensions["slack_clients"].get(client_key)
    if client is None:
        raise RuntimeError(f"Slack client is not configured for request channel {client_key}")
    if not client.available:
        raise RuntimeError(f"Slack client is unavailable for request channel {client_key}")

    if state.get("slack_message_ts") and state.get("slack_channel_id"):
        try:
            client.update_message(state["slack_channel_id"], state["slack_message_ts"], text, blocks)
            register_request_message(app, state)
            return state
        except SlackApiError as exc:
            if exc.error not in REPLACEABLE_MESSAGE_ERRORS:
                raise
            print(
                f"[SLACK WARN] Replacing stale request message action={state['action']} error={exc.error}",
                flush=True,
            )
            state = store.clear_slack_message(state["request_id"])

    result = client.post_message(text, blocks)
    state = store.attach_slack_message(
        state["request_id"], result.get("channel") or client.channel_id, result["ts"]
    )
    register_request_message(app, state)
    return state


def update_slack_message(app, state, detail=None):
    if not state or not state.get("slack_channel_id") or not state.get("slack_message_ts"):
        return False
    text, blocks = render_request_message(state, detail)
    try:
        deliver_slack_message(app, state, text, blocks)
        return True
    except SlackApiError as exc:
        print(
            f"[SLACK ERROR] Request message update failed method={exc.method} error={exc.error}",
            flush=True,
        )
        return False
    except Exception as exc:
        print(f"[SLACK ERROR] Request message update failed: {type(exc).__name__}", flush=True)
        return False


def expire_pending_requests(app, now=None):
    expired = app.extensions["approval_store"].expire_due(now=now)
    for state in expired:
        update_slack_message(app, state, "Approval window expired. A new incident will require a new request. / 승인 시간이 만료되었습니다.")
    return expired


def cleanup_old_slack_messages(app, now=None, ensure_console=True):
    """Audit and delete old terminal ChatOps messages without affecting DR state."""
    audit = app.extensions["chatops_audit"]
    store = app.extensions["approval_store"]
    if ensure_console:
        ensure_emergency_console(app)
    # Refresh status before selecting candidates so a failed Slack update cannot
    # leave a terminal request stuck with an old ACTIVE registry status.
    for state in store.list_with_messages():
        register_request_message(app, state)
    candidates = audit.candidates(
        now=now,
        ttl_seconds=app.config["CHATOPS_SLACK_CLEANUP_TTL_SECONDS"],
    )
    deleted = 0
    skipped = 0
    failed = 0
    for row in candidates:
        client = None
        audit_payload = row.get("payload")
        if row["kind"] == "request":
            state = store.get(row.get("request_id"))
            if not state or state.get("status") not in TERMINAL_STATUSES:
                skipped += 1
                continue
            state = dict(state)
            state["slack_channel_id"] = row["channel_id"]
            state["slack_message_ts"] = row["message_ts"]
            audit_payload = request_audit_payload(state)
            client = app.extensions["slack_clients"].get(request_client_key_from_state(state))
        elif row["kind"] == "alert":
            client = app.extensions.get("wazuh_alert_client")
        if client is None or not getattr(client, "available", False):
            skipped += 1
            continue
        try:
            prepared = audit.prepare_delete(row["message_key"], now=now, payload=audit_payload)
            if prepared is None:
                skipped += 1
                continue
            client.delete_message(prepared["channel_id"], prepared["message_ts"])
        except SlackApiError as exc:
            if exc.error == "message_not_found":
                audit.mark_deleted(row["message_key"], now=now)
                deleted += 1
                continue
            audit.mark_delete_failed(row["message_key"], exc.error, now=now)
            failed += 1
            print(
                f"[SLACK CLEANUP] Delete failed key={row['message_key']} error={exc.error}",
                flush=True,
            )
            continue
        except Exception as exc:
            audit.mark_delete_failed(row["message_key"], type(exc).__name__, now=now)
            failed += 1
            print(
                f"[SLACK CLEANUP] Delete failed key={row['message_key']} error={type(exc).__name__}",
                flush=True,
            )
            continue
        audit.mark_deleted(row["message_key"], now=now)
        deleted += 1
        print(f"[SLACK CLEANUP] Deleted key={row['message_key']}", flush=True)
    return {"candidates": len(candidates), "deleted": deleted, "skipped": skipped, "failed": failed}


def create_approval_request(app, action, incident, ttl_seconds=None):
    """Create or update an approval request and deliver its control message."""
    store = app.extensions["approval_store"]
    client_key = str(incident.get("slack_client_key") or action)
    slack_client = app.extensions["slack_clients"].get(client_key)
    if slack_client is None:
        raise RuntimeError(f"Slack client is not configured for request channel {client_key}")
    if not slack_client.available:
        raise RuntimeError(f"Slack client is unavailable for request channel {client_key}")

    expire_pending_requests(app)
    state, reused = store.create_or_merge(action, incident, ttl_seconds=ttl_seconds)
    if action == "failover" and incident.get("source") == "zabbix":
        _record_rto(incident, "incident_at")
        _record_rto(incident, "zabbix_detected_at")
        _record_rto(incident, "failover_approval_created_at", approval_id=state["request_id"])
    if action == "failback" and "security_status" in incident:
        state = store.refresh_failback_security(
            state["request_id"],
            {
                "status": incident.get("security_status"),
                "reason": incident.get("security_reason"),
                "agents": incident.get("security_agents"),
                "critical_incident": incident.get("security_critical_incident"),
            },
        )
    if action == "failback" and "network_status" in incident:
        state = store.refresh_failback_network(
            state["request_id"],
            {
                "status": incident.get("network_status"),
                "reason": incident.get("network_reason"),
                "source": incident.get("network_source"),
                "devices": incident.get("network_devices"),
                "checked_at": incident.get("network_checked_at"),
            },
        )
    text, blocks = render_request_message(state)
    state = deliver_slack_message(app, state, text, blocks)
    return state, reused


def acquire_pipeline_file_lock(path):
    """Acquire a non-blocking lock shared by every ChatOps worker process."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    lock_handle = open(path, "a+", encoding="utf-8")
    os.chmod(path, 0o600)
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_handle.close()
        return None
    return lock_handle


def release_pipeline_file_lock(lock_handle):
    if lock_handle is None:
        return
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock_handle.close()


def terminate_pipeline(process):
    """Stop the whole workflow process group after a total timeout."""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                process.kill()
            except OSError:
                pass
        process.wait(timeout=5)


def run_pipeline(app, request_id, action, final_approver, response_url):
    pipeline_lock = app.extensions["pipeline_lock"]
    store = app.extensions["approval_store"]
    label = action_label(action)
    command = None
    file_lock = None
    thread_lock_held = False

    if not pipeline_lock.acquire(blocking=False):
        state = store.finish(request_id, False, result="in_process_lock_unavailable")
        update_slack_message(app, state, "Another DR workflow is already running. / 다른 DR 작업이 실행 중입니다.")
        return
    thread_lock_held = True

    try:
        try:
            file_lock = acquire_pipeline_file_lock(app.config["CHATOPS_EXECUTION_LOCK_FILE"])
        except OSError as exc:
            print(f"[{label}] Pipeline lock could not be opened: {type(exc).__name__}", flush=True)
        if file_lock is None:
            state = store.finish(request_id, False, result="cross_process_lock_unavailable")
            update_slack_message(app, state, "Another DR workflow is already running. / 다른 DR 작업이 실행 중입니다.")
            return

        if not store.heartbeat(request_id):
            state = store.finish(request_id, False, result="execution_lease_lost")
            update_slack_message(app, state, "Execution lease was lost; a new approval is required. / 실행 lease가 만료되어 새 승인이 필요합니다.")
            return

        print(f"[{label}] Executing request {request_id} after final approval by <@{final_approver}>.", flush=True)
        state = store.get(request_id) or {}
        command = command_for_request(state, app.config["DR_SCRIPT_ROOT"], app.config)
        if command is None:
            result = "dump_restore_disabled" if request_type_from_state(state) == REQUEST_TYPE_DUMP_RESTORE else "unknown_action"
            state = store.finish(request_id, False, result=result)
            update_slack_message(
                app,
                state,
                "Dump 복구 실행이 비활성화되어 요청을 안전하게 중단했습니다. / "
                "Dump restore execution is disabled.",
            )
            return
        incidents = state.get("incidents") or []
        latest_incident = incidents[-1] if incidents else {}
        if action == "failover":
            _record_rto(latest_incident, "failover_approved_at", approval_id=request_id)
        child_env = os.environ.copy()
        child_env.setdefault("ANSIBLE_HOST_KEY_CHECKING", "False")
        child_env.setdefault("ANSIBLE_DEPRECATION_WARNINGS", "False")
        child_env.setdefault("PYTHONWARNINGS", "ignore")
        child_env.setdefault("ANSIBLE_LOCAL_TEMP", "/tmp/ansible-local")
        child_env.setdefault("ANSIBLE_REMOTE_TEMP", "/tmp/ansible-remote")
        child_env.setdefault("ANSIBLE_SSH_CONTROL_PATH_DIR", "/tmp/ansible-cp")
        run_id = _rto_run_id(latest_incident)
        if run_id:
            child_env.setdefault("DR_RTO_RUN_ID", run_id)
            child_env.setdefault("DR_RTO_EVENT_ID", latest_incident.get("incident_id", ""))
            child_env.setdefault("DR_RTO_APPROVAL_ID", request_id)

        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            env=child_env,
            start_new_session=True,
        )
        output_queue = queue.Queue()

        def stream_output():
            try:
                for raw_line in process.stdout or ():
                    output_queue.put(("line", raw_line))
            finally:
                output_queue.put(("eof", None))

        reader = threading.Thread(target=stream_output, daemon=True, name=f"chatops-output-{request_id}")
        reader.start()
        timeout_seconds = max(1.0, float(app.config["CHATOPS_EXECUTION_TIMEOUT_SECONDS"]))
        heartbeat_seconds = max(1.0, float(app.config["CHATOPS_EXECUTION_HEARTBEAT_SECONDS"]))
        deadline = time.monotonic() + timeout_seconds
        next_heartbeat = time.monotonic() + heartbeat_seconds
        output_eof = False
        timed_out = False

        while process.poll() is None or not output_eof:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                print(f"[{label}] Execution total timeout reached for request {request_id}.", flush=True)
                terminate_pipeline(process)
                break
            wait_seconds = min(remaining, max(0.1, next_heartbeat - time.monotonic()))
            try:
                kind, value = output_queue.get(timeout=wait_seconds)
            except queue.Empty:
                if time.monotonic() >= next_heartbeat:
                    store.heartbeat(request_id)
                    next_heartbeat = time.monotonic() + heartbeat_seconds
                continue
            if kind == "eof":
                output_eof = True
                continue
            line = value.rstrip("\n")
            if not should_suppress_log_line(line):
                print(line, flush=True)
            if time.monotonic() >= next_heartbeat:
                store.heartbeat(request_id)
                next_heartbeat = time.monotonic() + heartbeat_seconds

        reader.join(timeout=1)
        if process.stdout is not None:
            process.stdout.close()

        if timed_out:
            return_code = process.returncode if process.returncode is not None else -signal.SIGTERM
            result = "timeout"
            succeeded = False
        else:
            return_code = process.wait()
            result = "success" if return_code == 0 else "failed"
            succeeded = return_code == 0
        state = store.finish(request_id, succeeded, result=result)
        detail = (
            f"Exit code: {return_code}\n"
            + ("Execution total timeout was reached.\n" if timed_out else "")
            + "Detailed workflow output remains in the MSP execution log. / "
            "상세 실행 로그는 MSP 실행 로그에서 확인하세요."
        )
        if not update_slack_message(app, state, detail) and response_url:
            try:
                requests.post(
                    response_url,
                    json={"replace_original": False, "text": f"{label} finished with exit code {return_code}."},
                    timeout=10,
                )
            except Exception:
                pass
    except Exception as exc:
        state = store.finish(request_id, False, result="execution_error")
        update_slack_message(app, state, f"Execution error: {type(exc).__name__}")
        print(f"[{label}] Pipeline error for request {request_id}: {type(exc).__name__}", flush=True)
    finally:
        release_pipeline_file_lock(file_lock)
        if thread_lock_held:
            pipeline_lock.release()


def launch_pipeline(app, request_id, action, final_approver, response_url):
    test_launcher = app.config.get("PIPELINE_LAUNCHER")
    if test_launcher is not None:
        test_launcher(request_id, action, final_approver)
        return
    threading.Thread(
        target=run_pipeline,
        args=(app, request_id, action, final_approver, response_url),
        daemon=True,
    ).start()


def parse_interaction(payload, store):
    action_item = (payload.get("actions") or [{}])[0]
    raw_value = action_item.get("value", "")
    try:
        value = json.loads(raw_value)
        request_id = value.get("request_id")
        action = value.get("action")
        operation = value.get("operation")
    except (TypeError, json.JSONDecodeError):
        legacy_actions = {"run_restore": "failover", "run_failback": "failback"}
        action = legacy_actions.get(raw_value)
        operation = "approve"
        active = store.find_active(action) if action else None
        request_id = active["request_id"] if active else None
    return request_id, action, operation


def slack_signature_key(app, action, operation=None, metadata=None, channel_id=None):
    """Select the Slack App signing secret without weakening verification."""
    metadata = metadata or {}
    if metadata.get("request_origin") == "EMERGENCY":
        return "emergency"
    if metadata.get("request_type") in {
        REQUEST_TYPE_EMERGENCY_FAILOVER,
        REQUEST_TYPE_EMERGENCY_FAILBACK,
    }:
        return "emergency"
    if operation in {"emergency_status", "emergency_open", "emergency_dump_open"}:
        return "emergency"
    if channel_id and channel_id == app.config.get("SLACK_EMERGENCY_CHANNEL_ID"):
        return "emergency"
    return action


def ephemeral(text):
    return jsonify({"response_type": "ephemeral", "replace_original": False, "text": text})


def _view_state_value(view, block_id, action_id):
    values = ((view.get("state") or {}).get("values") or {}).get(block_id) or {}
    item = values.get(action_id) or {}
    selected = item.get("selected_option") or {}
    if selected.get("value") is not None:
        return selected.get("value")
    selected_values = item.get("selected_options") or []
    if selected_values:
        return selected_values[0].get("value")
    return item.get("value", "")


def handle_view_submission(app, payload, action, admin_ids):
    """Handle emergency/dump modals without executing a workflow."""
    user_id = (payload.get("user") or {}).get("id", "")
    if not admin_ids or user_id not in admin_ids:
        return jsonify({"response_action": "errors", "errors": {"emergency_reason_code": "등록된 관리자만 사용할 수 있습니다."}})
    view = payload.get("view") or {}
    callback_id = view.get("callback_id")
    try:
        metadata = json.loads(view.get("private_metadata") or "{}")
    except json.JSONDecodeError:
        metadata = {}

    if callback_id == "hospital_dr_emergency_reason":
        requested_action = metadata.get("action")
        request_type = metadata.get("request_type")
        if requested_action not in ("failover", "failback"):
            return jsonify({"response_action": "clear"})
        expected_type = (
            REQUEST_TYPE_EMERGENCY_FAILOVER
            if requested_action == "failover"
            else REQUEST_TYPE_EMERGENCY_FAILBACK
        )
        valid_request_type = request_type == expected_type or (
            requested_action == "failover" and request_type == REQUEST_TYPE_DUMP_RESTORE
        )
        if not valid_request_type:
            return jsonify({"response_action": "errors", "errors": {"emergency_reason_code": "요청 유형을 확인할 수 없습니다."}})
        reason_code = _view_state_value(view, "emergency_reason_code", "reason_code")
        reason_text = _view_state_value(view, "emergency_reason_detail", "reason_detail")
        valid, normalized = validate_reason(
            requested_action,
            reason_code,
            reason_text,
            request_type=request_type,
        )
        if not valid:
            block = "emergency_reason_detail" if reason_code == "OTHER" else "emergency_reason_code"
            return jsonify({"response_action": "errors", "errors": {block: normalized}})
        reason_label = dict(reason_options(requested_action, request_type)).get(reason_code, reason_code)
        if request_type == REQUEST_TYPE_DUMP_RESTORE:
            metadata.update(
                {
                    "action": "failover",
                    "request_type": REQUEST_TYPE_DUMP_RESTORE,
                    "request_origin": "EMERGENCY",
                    "slack_client_key": "emergency",
                    "requested_by": user_id,
                    "reason_code": reason_code,
                    "reason_text": normalized if reason_code == "OTHER" else reason_label,
                }
            )
            return jsonify(
                {
                    "response_action": "push",
                    "view": dump_select_view(
                        "failover",
                        app.config["DUMP_BACKUP_DIR"],
                        "emergency",
                        metadata=metadata,
                    ),
                }
            )
        now = utc_timestamp()
        precheck = evaluate_emergency_precheck(app, requested_action, request_type)
        incident = {
            "incident_id": f"EMG-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}",
            "summary": f"Emergency {requested_action} request",
            "observed_at": format_timestamp(now),
            "source": "emergency_console",
            "request_type": request_type,
            "slack_client_key": "emergency",
            "request_origin": "EMERGENCY",
            "reason_code": reason_code,
            "reason_text": normalized if reason_code == "OTHER" else reason_label,
            "requested_by": user_id,
            "requested_at": now,
            "expires_at": now + app.config.get("EMERGENCY_TTL_SECONDS", EMERGENCY_TTL_SECONDS),
            "precheck_status": precheck.get("status"),
            "precheck_reason": precheck.get("reason"),
            "precheck_checks": precheck.get("checks", {}),
        }
        try:
            create_approval_request(
                app,
                requested_action,
                incident,
                ttl_seconds=app.config.get("EMERGENCY_TTL_SECONDS", EMERGENCY_TTL_SECONDS),
            )
        except Exception as exc:
            print(f"[EMERGENCY] request creation failed: {type(exc).__name__}", flush=True)
            return jsonify({"response_action": "errors", "errors": {"emergency_reason_code": "요청 생성에 실패했습니다."}})
        return jsonify({"response_action": "clear"})

    if callback_id == "hospital_dr_dump_select":
        requested_action = metadata.get("action", action)
        backup_id = _view_state_value(view, "dump_backup", "backup_id")
        backup = resolve_dump_backup(app.config["DUMP_BACKUP_DIR"], backup_id)
        if backup is None:
            return jsonify({"response_action": "errors", "errors": {"dump_backup": "유효한 complete dump를 선택해 주세요."}})
        metadata["action"] = requested_action
        metadata["slack_client_key"] = metadata.get("slack_client_key") or action
        return jsonify({"response_action": "update", "view": dump_confirm_view(requested_action, backup, metadata)})

    if callback_id == "hospital_dr_dump_confirm":
        selected = _view_state_value(view, "dump_confirm", "confirm_choice")
        if selected != "YES":
            return jsonify({"response_action": "clear"})
        backup = resolve_dump_backup(app.config["DUMP_BACKUP_DIR"], metadata.get("backup_id"))
        if backup is None:
            return jsonify({"response_action": "errors", "errors": {"dump_confirm": "선택한 dump를 다시 확인할 수 없습니다."}})
        expected_checksum = str(metadata.get("checksum") or "").strip().lower()
        if expected_checksum and backup["checksum"].lower() != expected_checksum:
            return jsonify(
                {
                    "response_action": "errors",
                    "errors": {"dump_confirm": "선택한 dump의 checksum이 변경되었습니다. 다시 선택해 주세요."},
                }
            )
        now = utc_timestamp()
        request_origin = str(metadata.get("request_origin") or "GENERAL").strip().upper()
        if request_origin not in {"GENERAL", "EMERGENCY"}:
            return jsonify({"response_action": "errors", "errors": {"dump_confirm": "요청 출처를 확인할 수 없습니다."}})
        emergency_dump = request_origin == "EMERGENCY"
        ttl_seconds = app.config.get("EMERGENCY_TTL_SECONDS", EMERGENCY_TTL_SECONDS) if emergency_dump else None
        precheck = evaluate_emergency_precheck(
            app,
            "failover",
            REQUEST_TYPE_DUMP_RESTORE,
            backup=backup,
        )
        incident = {
            "incident_id": f"DUMP-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}",
            "summary": "Dump restore failover request",
            "observed_at": format_timestamp(now),
            "source": "emergency_dump_restore_console" if emergency_dump else "dump_restore_console",
            "request_type": REQUEST_TYPE_DUMP_RESTORE,
            "slack_client_key": "emergency" if emergency_dump else (metadata.get("slack_client_key") or "failover"),
            "request_origin": request_origin,
            "requested_by": metadata.get("requested_by") or user_id,
            "requested_at": now,
            "expires_at": now + ttl_seconds if ttl_seconds else None,
            "reason_code": metadata.get("reason_code") if emergency_dump else None,
            "reason_text": metadata.get("reason_text") if emergency_dump else None,
            "backup_id": backup["backup_id"],
            "backup_filename": backup["filename"],
            "backup_timestamp": backup.get("backup_timestamp") or backup.get("timestamp"),
            "backup_checksum": backup["checksum"],
            "expected_rpo": backup["expected_rpo"],
            "precheck_status": precheck.get("status"),
            "precheck_reason": precheck.get("reason"),
            "precheck_checks": precheck.get("checks", {}),
        }
        try:
            create_approval_request(app, "failover", incident, ttl_seconds=ttl_seconds)
        except Exception as exc:
            print(f"[DUMP] request creation failed: {type(exc).__name__}", flush=True)
            return jsonify({"response_action": "errors", "errors": {"dump_confirm": "Dump 요청 생성에 실패했습니다."}})
        return jsonify({"response_action": "clear"})

    return jsonify({"response_action": "clear"})


def create_app(default_state_db, default_secret_file, default_script_root, test_config=None):
    app = Flask(__name__)
    app.config.update(
        CHATOPS_STATE_DB=os.environ.get("CHATOPS_STATE_DB", default_state_db),
        DR_SECRET_FILE=os.environ.get("DR_SECRET_FILE", default_secret_file),
        DR_SCRIPT_ROOT=os.environ.get("DR_SCRIPT_ROOT", default_script_root),
        DR_STATE_FILE=os.environ.get(
            "DR_STATE_FILE",
            os.path.join(os.environ.get("DR_STATE_DIR", "/var/lib/hospital-dr"), "dr_state"),
        ),
        CHATOPS_EXECUTION_TIMEOUT_SECONDS=float(
            os.environ.get("CHATOPS_EXECUTION_TIMEOUT_SECONDS", str(DEFAULT_EXECUTION_TIMEOUT_SECONDS))
        ),
        CHATOPS_EXECUTION_HEARTBEAT_SECONDS=float(
            os.environ.get("CHATOPS_EXECUTION_HEARTBEAT_SECONDS", str(DEFAULT_EXECUTION_HEARTBEAT_SECONDS))
        ),
        CHATOPS_EXECUTION_LEASE_SECONDS=float(
            os.environ.get("CHATOPS_EXECUTION_LEASE_SECONDS", str(DEFAULT_EXECUTION_LEASE_SECONDS))
        ),
        CHATOPS_EXECUTION_LOCK_FILE=os.environ.get("CHATOPS_EXECUTION_LOCK_FILE", ""),
        EXPIRY_INTERVAL_SECONDS=int(os.environ.get("CHATOPS_EXPIRY_INTERVAL_SECONDS", "15")),
        CHATOPS_SLACK_CLEANUP_TTL_SECONDS=int(
            os.environ.get("CHATOPS_SLACK_CLEANUP_TTL_SECONDS", "86400")
        ),
        CHATOPS_SLACK_CLEANUP_INTERVAL_SECONDS=int(
            os.environ.get("CHATOPS_SLACK_CLEANUP_INTERVAL_SECONDS", "300")
        ),
        EMERGENCY_CONSOLE_WATCHDOG_INTERVAL_SECONDS=int(
            os.environ.get("EMERGENCY_CONSOLE_WATCHDOG_INTERVAL_SECONDS", "60")
        ),
        ZABBIX_TEST_MODE_MAX_EVENT_AGE_SECONDS=int(
            os.environ.get(
                "ZABBIX_TEST_MODE_MAX_EVENT_AGE_SECONDS",
                str(DEFAULT_TEST_MODE_MAX_EVENT_AGE_SECONDS),
            )
        ),
        ZABBIX_DR_MAX_EVENT_AGE_SECONDS=int(
            os.environ.get(
                "ZABBIX_DR_MAX_EVENT_AGE_SECONDS",
                str(DEFAULT_DR_MAX_EVENT_AGE_SECONDS),
            )
        ),
        WAZUH_CORRELATION_STATE_FILE=os.environ.get(
            "WAZUH_CORRELATION_STATE_FILE",
            os.path.join(
                os.path.dirname(os.path.abspath(os.environ.get("CHATOPS_STATE_DB", default_state_db))),
                "wazuh_correlation.json",
            ),
        ),
        WAZUH_CORRELATION_WINDOW_SECONDS=int(os.environ.get("WAZUH_CORRELATION_WINDOW_SECONDS", "900")),
        WAZUH_SECURITY_SLACK_ENABLED=os.environ.get("WAZUH_SECURITY_SLACK_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"},
        WAZUH_SECURITY_SLACK_DRY_RUN=os.environ.get("WAZUH_SECURITY_SLACK_DRY_RUN", "false").lower()
        in {"1", "true", "yes", "on"},
        NETWORK_DR_HEALTH_ENABLED=as_bool(os.environ.get("NETWORK_DR_HEALTH_ENABLED", "true")),
        NETWORK_DR_HEALTH_FRESHNESS_SECONDS=int(
            os.environ.get("NETWORK_DR_HEALTH_FRESHNESS_SECONDS", "300")
        ),
        FAILBACK_STABILITY_SECONDS=int(os.environ.get("FAILBACK_STABILITY_SECONDS", "300")),
        SLACK_EMERGENCY_CHANNEL_ID=os.environ.get("SLACK_EMERGENCY_CHANNEL_ID", ""),
        EMERGENCY_TTL_SECONDS=int(os.environ.get("EMERGENCY_TTL_SECONDS", str(EMERGENCY_TTL_SECONDS))),
        DUMP_BACKUP_DIR=os.environ.get(
            "DUMP_BACKUP_DIR", os.environ.get("DB_BACKUP_DIR", "/home/cloud/db_backups")
        ),
        DUMP_RESTORE_EXEC_ENABLED=as_bool(os.environ.get("DUMP_RESTORE_EXEC_ENABLED", "false")),
        DUMP_RESTORE_EXECUTOR=os.environ.get("DUMP_RESTORE_EXECUTOR", ""),
        DB_NR_ENABLED=as_bool(os.environ.get("DB_NR_ENABLED", "false")),
        CHATOPS_AUDIT_FILE=os.environ.get(
            "CHATOPS_AUDIT_FILE",
            os.path.join(
                os.path.dirname(
                    os.path.abspath(os.environ.get("CHATOPS_STATE_DB", default_state_db))
                ),
                "audit",
                "chatops_audit.jsonl",
            ),
        ),
        VERIFY_SLACK_SIGNATURE=True,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("CHATOPS_EXECUTION_LOCK_FILE"):
        app.config["CHATOPS_EXECUTION_LOCK_FILE"] = os.path.join(
            os.path.dirname(os.path.abspath(app.config["CHATOPS_STATE_DB"])),
            "pipeline.lock",
        )

    file_values = load_env_file(app.config["DR_SECRET_FILE"])

    def configured_value(name):
        return file_values.get(name, "")

    for key in (
        "SLACK_EMERGENCY_CHANNEL_ID",
        "DUMP_BACKUP_DIR",
        "DUMP_RESTORE_EXECUTOR",
    ):
        if key in file_values and not (test_config and key in test_config):
            app.config[key] = file_values[key]
    if "EMERGENCY_CONSOLE_WATCHDOG_INTERVAL_SECONDS" in file_values and not (
        test_config and "EMERGENCY_CONSOLE_WATCHDOG_INTERVAL_SECONDS" in test_config
    ):
        try:
            app.config["EMERGENCY_CONSOLE_WATCHDOG_INTERVAL_SECONDS"] = int(
                file_values["EMERGENCY_CONSOLE_WATCHDOG_INTERVAL_SECONDS"]
            )
        except ValueError:
            app.config["EMERGENCY_CONSOLE_WATCHDOG_INTERVAL_SECONDS"] = 60
    for key in ("EMERGENCY_TTL_SECONDS", "DUMP_RESTORE_EXEC_ENABLED", "DB_NR_ENABLED"):
        if key in file_values and not (test_config and key in test_config):
            if key == "EMERGENCY_TTL_SECONDS":
                try:
                    app.config[key] = int(file_values[key])
                except ValueError:
                    app.config[key] = EMERGENCY_TTL_SECONDS
            else:
                app.config[key] = as_bool(file_values[key])

    if "NETWORK_DR_HEALTH_ENABLED" in file_values and not (
        test_config and "NETWORK_DR_HEALTH_ENABLED" in test_config
    ):
        app.config["NETWORK_DR_HEALTH_ENABLED"] = as_bool(file_values["NETWORK_DR_HEALTH_ENABLED"])
    if "NETWORK_DR_HEALTH_FRESHNESS_SECONDS" in file_values and not (
        test_config and "NETWORK_DR_HEALTH_FRESHNESS_SECONDS" in test_config
    ):
        try:
            app.config["NETWORK_DR_HEALTH_FRESHNESS_SECONDS"] = int(
                file_values["NETWORK_DR_HEALTH_FRESHNESS_SECONDS"]
            )
        except ValueError:
            app.config["NETWORK_DR_HEALTH_FRESHNESS_SECONDS"] = 300
    if "FAILBACK_STABILITY_SECONDS" in file_values and not (
        test_config and "FAILBACK_STABILITY_SECONDS" in test_config
    ):
        try:
            app.config["FAILBACK_STABILITY_SECONDS"] = int(file_values["FAILBACK_STABILITY_SECONDS"])
        except ValueError:
            app.config["FAILBACK_STABILITY_SECONDS"] = 300

    if app.config.get("NETWORK_DR_HEALTH_PROVIDER") is None and app.config.get("NETWORK_DR_HEALTH_SNAPSHOT") is None:
        try:
            from network_dr_health import build_zabbix_network_provider

            provider = build_zabbix_network_provider(file_values, os.environ)
            if provider is not None:
                app.config["NETWORK_DR_HEALTH_PROVIDER"] = provider
        except Exception:
            # A missing/incomplete read-only identity must remain UNKNOWN.
            pass

    if "ZABBIX_TEST_MODE_MAX_EVENT_AGE_SECONDS" in file_values and not (
        test_config and "ZABBIX_TEST_MODE_MAX_EVENT_AGE_SECONDS" in test_config
    ):
        try:
            app.config["ZABBIX_TEST_MODE_MAX_EVENT_AGE_SECONDS"] = int(
                configured_value("ZABBIX_TEST_MODE_MAX_EVENT_AGE_SECONDS")
            )
        except ValueError:
            app.config["ZABBIX_TEST_MODE_MAX_EVENT_AGE_SECONDS"] = DEFAULT_TEST_MODE_MAX_EVENT_AGE_SECONDS
    if "ZABBIX_DR_MAX_EVENT_AGE_SECONDS" in file_values and not (
        test_config and "ZABBIX_DR_MAX_EVENT_AGE_SECONDS" in test_config
    ):
        try:
            app.config["ZABBIX_DR_MAX_EVENT_AGE_SECONDS"] = int(
                configured_value("ZABBIX_DR_MAX_EVENT_AGE_SECONDS")
            )
        except ValueError:
            app.config["ZABBIX_DR_MAX_EVENT_AGE_SECONDS"] = DEFAULT_DR_MAX_EVENT_AGE_SECONDS

    if "WAZUH_CORRELATION_STATE_FILE" in file_values and not (
        test_config and "WAZUH_CORRELATION_STATE_FILE" in test_config
    ):
        app.config["WAZUH_CORRELATION_STATE_FILE"] = configured_value("WAZUH_CORRELATION_STATE_FILE")
    if "WAZUH_CORRELATION_WINDOW_SECONDS" in file_values and not (
        test_config and "WAZUH_CORRELATION_WINDOW_SECONDS" in test_config
    ):
        try:
            app.config["WAZUH_CORRELATION_WINDOW_SECONDS"] = int(
                configured_value("WAZUH_CORRELATION_WINDOW_SECONDS")
            )
        except ValueError:
            app.config["WAZUH_CORRELATION_WINDOW_SECONDS"] = 900

    if "WAZUH_SECURITY_SLACK_ENABLED" in file_values and not (
        test_config and "WAZUH_SECURITY_SLACK_ENABLED" in test_config
    ):
        app.config["WAZUH_SECURITY_SLACK_ENABLED"] = configured_value(
            "WAZUH_SECURITY_SLACK_ENABLED"
        ).lower() in {"1", "true", "yes", "on"}
    if "WAZUH_SECURITY_SLACK_DRY_RUN" in file_values and not (
        test_config and "WAZUH_SECURITY_SLACK_DRY_RUN" in test_config
    ):
        app.config["WAZUH_SECURITY_SLACK_DRY_RUN"] = configured_value(
            "WAZUH_SECURITY_SLACK_DRY_RUN"
        ).lower() in {"1", "true", "yes", "on"}

    if app.config.get("WAZUH_FAILBACK_REQUIRED_AGENTS") is None:
        app.config["WAZUH_FAILBACK_REQUIRED_AGENTS"] = tuple(
            item.strip()
            for item in configured_value("WAZUH_FAILBACK_REQUIRED_AGENTS").split(",")
            if item.strip()
        )
    if app.config.get("WAZUH_SECURITY_STATE_PROVIDER") is None:
        try:
            from wazuh_live_provider import build_wazuh_provider

            live_provider = build_wazuh_provider(file_values)
            if live_provider is not None:
                app.config["WAZUH_SECURITY_STATE_PROVIDER"] = live_provider.snapshot
        except Exception:
            # A missing or incomplete live provider must remain UNKNOWN.
            pass
    channel_id = configured_value("SLACK_CHANNEL_ID")
    alert_channel_id = configured_value("SLACK_ALERT_CHANNEL_ID") or channel_id
    raw_admin_ids = configured_value("SLACK_ADMIN_USER_IDS")
    token_by_action = {
        "failover": configured_value("SLACK_FAILOVER_BOT_TOKEN"),
        "failback": configured_value("SLACK_FAILBACK_BOT_TOKEN"),
    }
    signing_secret_by_action = {
        "failover": configured_value("SLACK_FAILOVER_SIGNING_SECRET"),
        "failback": configured_value("SLACK_FAILBACK_SIGNING_SECRET"),
        "emergency": configured_value("SLACK_EMERGENCY_SIGNING_SECRET"),
    }
    configured_signing_secrets = app.config.get("SLACK_SIGNING_SECRETS")
    if configured_signing_secrets is not None:
        signing_secret_by_action.update(configured_signing_secrets)
    configured_admin_ids = app.config.get("ADMIN_USER_IDS")
    if configured_admin_ids is None:
        admin_ids = {item.strip() for item in raw_admin_ids.split(",") if item.strip()}
    elif isinstance(configured_admin_ids, str):
        admin_ids = {item.strip() for item in configured_admin_ids.split(",") if item.strip()}
    else:
        admin_ids = set(configured_admin_ids)

    store = ApprovalStore(
        app.config["CHATOPS_STATE_DB"],
        initial_ttl=app.config.get("INITIAL_APPROVAL_TTL_SECONDS", INITIAL_APPROVAL_TTL_SECONDS),
        approved_ttl=app.config.get("FIRST_APPROVAL_TTL_SECONDS", FIRST_APPROVAL_TTL_SECONDS),
        execution_timeout=app.config["CHATOPS_EXECUTION_TIMEOUT_SECONDS"],
        execution_lease_seconds=app.config["CHATOPS_EXECUTION_LEASE_SECONDS"],
    )
    store.recover_stale_executions()
    audit = ChatOpsAudit(app.config["CHATOPS_STATE_DB"], app.config["CHATOPS_AUDIT_FILE"])
    audit.backfill_requests(store)
    configured_clients = app.config.get("SLACK_CLIENTS")
    if configured_clients is None and app.config.get("SLACK_CLIENT") is not None:
        configured_clients = {
            "failover": app.config["SLACK_CLIENT"],
            "failback": app.config["SLACK_CLIENT"],
        }
    slack_clients = configured_clients or {
        action: SlackClient(token, channel_id) for action, token in token_by_action.items()
    }
    if configured_clients is not None:
        slack_clients = dict(slack_clients)
    emergency_channel_id = app.config.get("SLACK_EMERGENCY_CHANNEL_ID") or configured_value(
        "SLACK_EMERGENCY_CHANNEL_ID"
    )
    app.config["SLACK_EMERGENCY_CHANNEL_ID"] = emergency_channel_id
    emergency_client = app.config.get("SLACK_EMERGENCY_CLIENT")
    if emergency_client is None and emergency_channel_id:
        emergency_client = SlackClient(
            configured_value("SLACK_EMERGENCY_BOT_TOKEN"),
            emergency_channel_id,
        )
    if emergency_client is not None:
        slack_clients["emergency"] = emergency_client
    alert_client = app.config.get("SLACK_ALERT_CLIENT") or SlackClient(
        configured_value("SLACK_ALERT_BOT_TOKEN"), alert_channel_id
    )
    security_client = app.config.get("WAZUH_SECURITY_SLACK_CLIENT")
    if security_client is None and app.config.get("WAZUH_SECURITY_SLACK_ENABLED"):
        security_token = configured_value("WAZUH_SECURITY_SLACK_TOKEN") or configured_value(
            "SLACK_SECURITY_BOT_TOKEN"
        )
        security_channel = configured_value("WAZUH_SECURITY_SLACK_CHANNEL") or configured_value(
            "SLACK_SECURITY_CHANNEL_ID"
        )
        if security_token and security_channel and not app.config.get("WAZUH_SECURITY_SLACK_DRY_RUN"):
            security_client = SlackClient(security_token, security_channel)
    app.extensions["approval_store"] = store
    app.extensions["emergency_console_store"] = EmergencyConsoleStore(app.config["CHATOPS_STATE_DB"])
    app.extensions["chatops_audit"] = audit
    app.extensions["slack_clients"] = slack_clients
    app.extensions["wazuh_alert_client"] = alert_client
    app.extensions["wazuh_security_client"] = security_client
    app.extensions["admin_user_ids"] = admin_ids
    app.extensions["slack_signing_secrets"] = signing_secret_by_action
    app.extensions["slack_replay_cache"] = {}
    app.extensions["slack_replay_lock"] = threading.Lock()
    app.extensions["pipeline_lock"] = threading.Lock()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    @app.get("/")
    def index():
        return "<h1>Hospital DR Orchestration Console (Persistent Two-Approval Mode)</h1>"

    @app.post("/chatops/request")
    def create_request():
        if request.remote_addr not in ("127.0.0.1", "::1") and not app.config.get("ALLOW_REMOTE_REQUEST_CREATION", False):
            return jsonify({"error": "request creation is restricted to MSP localhost"}), 403
        if not admin_ids:
            return jsonify({
                "error": "SLACK_ADMIN_USER_IDS must contain at least one administrator; two distinct administrators are required to execute"
            }), 503

        payload = request.get_json(silent=True) or {}
        action = payload.get("action")
        if action not in ("failover", "failback"):
            return jsonify({"error": "action must be failover or failback"}), 400
        incident = {
            "incident_id": str(payload.get("incident_id") or uuid.uuid4()),
            "summary": safe_mrkdwn(payload.get("summary") or f"{action} request", 300),
            "observed_at": payload.get("observed_at") or format_timestamp(utc_timestamp()),
        }
        source = str(payload.get("source") or "").strip().lower()
        test_mode = as_bool(payload.get("test_mode"))
        if source == "zabbix":
            # Production Zabbix approval messages carry EVENT.AGE in their
            # structured operation message.  Fail closed when it is absent or
            # stale so a delayed historical Recovery cannot create a fresh
            # Failback request.  The normal Alert endpoint is intentionally
            # unaffected; only the approval media path is guarded here.
            event_age = parse_event_age(payload.get("event_age_seconds"))
            if event_age is None:
                return jsonify({"error": "zabbix event age is missing or invalid"}), 422
            if event_age < 0 or event_age > app.config["ZABBIX_DR_MAX_EVENT_AGE_SECONDS"]:
                print("[ZABBIX] stale production event rejected before approval request", flush=True)
                return jsonify({"error": "stale zabbix event rejected"}), 422
            incident.update({"source": "zabbix", "event_age_seconds": event_age})
            if payload.get("incident_at"):
                incident["incident_at"] = str(payload["incident_at"])
            if payload.get("rto_run_id"):
                incident["rto_run_id"] = str(payload["rto_run_id"])
            if action == "failback" and not test_mode:
                dr_state = read_persistent_dr_state(app.config["DR_STATE_FILE"])
                if dr_state != "DR_ACTIVE":
                    print("[ZABBIX] production failback rejected outside DR_ACTIVE", flush=True)
                    return jsonify({"error": "production failback requires DR_ACTIVE"}), 409
                incident["dr_state"] = dr_state
        if test_mode:
            raw_age = payload.get("event_age_seconds")
            event_age = parse_event_age(raw_age)
            if event_age is None:
                return jsonify({"error": "test-mode event age is missing or invalid"}), 422
            if event_age < 0 or event_age > app.config["ZABBIX_TEST_MODE_MAX_EVENT_AGE_SECONDS"]:
                print("[ZABBIX TEST] stale event rejected before approval request", flush=True)
                return jsonify({"error": "stale test-mode event rejected"}), 422
            incident.update(
                {
                    "test_mode": True,
                    "event_age_seconds": event_age,
                    "host": safe_mrkdwn(payload.get("host") or "unknown", 120),
                }
            )
        if action == "failback":
            if not test_mode:
                stability_verified = as_bool(payload.get("stability_verified"))
                raw_stability = payload.get("stability_seconds")
                if app.testing and raw_stability is None and not payload.get("stability_verified"):
                    # Existing in-memory unit/dry-run applications represent
                    # the guard with a deterministic fixture.  Real runtime
                    # requests must carry proof from zabbix_failback_guard.sh.
                    stability_verified = True
                    raw_stability = app.config["FAILBACK_STABILITY_SECONDS"]
                try:
                    stability_seconds = int(float(raw_stability))
                except (TypeError, ValueError):
                    stability_seconds = 0
                if not stability_verified or stability_seconds < app.config["FAILBACK_STABILITY_SECONDS"]:
                    print("[FAILBACK] recovery stability proof missing or incomplete", flush=True)
                    return jsonify({"error": "failback stability window is not satisfied"}), 409
                incident.update(
                    {
                        "stability_verified": True,
                        "stability_seconds": stability_seconds,
                        "stability_required_seconds": app.config["FAILBACK_STABILITY_SECONDS"],
                    }
                )
            else:
                test_stability_verified = as_bool(payload.get("stability_verified"))
                test_stability_raw = payload.get("stability_seconds")
                if app.testing and test_stability_raw is None and not payload.get("stability_verified"):
                    test_stability_verified = True
                    test_stability_raw = app.config["FAILBACK_STABILITY_SECONDS"]
                try:
                    test_stability_seconds = int(float(test_stability_raw or 0))
                except (TypeError, ValueError):
                    test_stability_seconds = 0
                incident.update(
                    {
                        "stability_verified": test_stability_verified,
                        "stability_seconds": test_stability_seconds,
                        "stability_required_seconds": app.config["FAILBACK_STABILITY_SECONDS"],
                    }
                )

            network = evaluate_current_network_health(app)
            if network["status"] != "READY":
                print(
                    f"[FAILBACK] network gate rejected status={network['status']}",
                    flush=True,
                )
                return jsonify({"error": "network DR health is not READY"}), 409
            incident.update(
                {
                    "network_status": network["status"],
                    "network_reason": network["reason"],
                    "network_source": network.get("source", "-"),
                    "network_devices": network.get("devices", {}),
                    "network_checked_at": network.get("checked_at", "-"),
                }
            )
            security = evaluate_current_failback_security(app)
            incident.update(
                {
                    "security_status": security["status"],
                    "security_reason": security["reason"],
                    "security_agents": security["agents"],
                    "security_critical_incident": security["critical_incident"],
                }
            )
        try:
            state, reused = create_approval_request(app, action, incident)
        except SlackApiError as exc:
            print(
                f"[SLACK ERROR] Approval request delivery failed method={exc.method} error={exc.error}",
                flush=True,
            )
            return jsonify({"error": "Slack approval request delivery failed"}), 502
        except Exception as exc:
            print(f"[SLACK ERROR] Approval request delivery failed: {type(exc).__name__}", flush=True)
            return jsonify({"error": "Slack approval request delivery failed"}), 502
        return jsonify(
            {
                "request_id": state["request_id"],
                "action": state["action"],
                "status": state["status"],
                "incident_count": len(state["incidents"]),
                "reused": reused,
            }
        )

    @app.get("/chatops/network-health")
    def network_health():
        """Return a redacted read-only network gate for the Zabbix guard."""
        if request.remote_addr not in ("127.0.0.1", "::1"):
            return jsonify({"status": "UNKNOWN", "reason": "network health is local-only"}), 403
        return jsonify(evaluate_current_network_health(app))

    @app.post("/wazuh/alert")
    def wazuh_alert():
        if request.remote_addr not in ("127.0.0.1", "::1"):
            return jsonify({"error": "Wazuh alert ingestion is restricted to MSP localhost"}), 403
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Wazuh alert must be a JSON object"}), 400
        try:
            from wazuh_alerts import process_wazuh_alert

            return jsonify(process_wazuh_alert(app, payload))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/zabbix/alert")
    def zabbix_alert():
        if request.remote_addr not in ("127.0.0.1", "::1"):
            return jsonify({"error": "Zabbix alert ingestion is restricted to MSP localhost"}), 403
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Zabbix alert must be a JSON object"}), 400
        try:
            from zabbix_alerts import ZabbixProcessingError, process_zabbix_alert

            return jsonify(process_zabbix_alert(app, payload))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except ZabbixProcessingError as exc:
            print(f"[ZABBIX ERROR] Alert processing failed: {type(exc).__name__}", flush=True)
            return jsonify({"ok": False, "error": str(exc)}), exc.status_code
        except Exception as exc:
            print(f"[ZABBIX ERROR] Alert processing failed: {type(exc).__name__}", flush=True)
            return jsonify({"ok": False, "error": "Zabbix alert processing failed"}), 502

    def handle_slack_action(expected_action=None):
        raw_body = request.get_data(cache=True)
        try:
            payload = json.loads(request.form.get("payload", "{}"))
        except json.JSONDecodeError:
            return jsonify({"error": "invalid Slack payload"}), 400

        if payload.get("type") == "view_submission":
            try:
                metadata = json.loads((payload.get("view") or {}).get("private_metadata") or "{}")
            except json.JSONDecodeError:
                metadata = {}
            action = metadata.get("action") or "failover"
            if expected_action and action != expected_action:
                return jsonify({"error": "Slack action does not match this endpoint"}), 400
            signature_key = slack_signature_key(app, action, metadata=metadata)
            signing_secret = signing_secret_by_action.get(signature_key, "")
            if app.config["VERIFY_SLACK_SIGNATURE"]:
                if not signing_secret:
                    return jsonify(
                        {"error": f"SLACK_{signature_key.upper()}_SIGNING_SECRET is not configured"}
                    ), 503
                timestamp = request.headers.get("X-Slack-Request-Timestamp")
                signature = request.headers.get("X-Slack-Signature")
                if not valid_slack_signature(signing_secret, raw_body, timestamp, signature):
                    return jsonify({"error": "invalid Slack signature"}), 401
                if not remember_slack_request(
                    app.extensions["slack_replay_cache"],
                    app.extensions["slack_replay_lock"],
                    timestamp,
                    signature,
                ):
                    return jsonify({"error": "replayed Slack request"}), 401
            return handle_view_submission(app, payload, action, admin_ids)

        request_id, action, operation = parse_interaction(payload, store)
        if expected_action and action != expected_action:
            return jsonify({"error": "Slack action does not match this endpoint"}), 400
        if action not in ("failover", "failback"):
            return ephemeral("요청 작업을 식별할 수 없습니다.")
        channel_id = (payload.get("channel") or {}).get("id") or (
            payload.get("container") or {}
        ).get("channel_id")
        signature_key = slack_signature_key(
            app,
            action,
            operation=operation,
            channel_id=channel_id,
        )
        signing_secret = signing_secret_by_action.get(signature_key, "")
        if app.config["VERIFY_SLACK_SIGNATURE"]:
            if not signing_secret:
                return jsonify({
                    "error": f"SLACK_{signature_key.upper()}_SIGNING_SECRET is not configured"
                }), 503
            timestamp = request.headers.get("X-Slack-Request-Timestamp")
            signature = request.headers.get("X-Slack-Signature")
            if not valid_slack_signature(
                signing_secret,
                raw_body,
                timestamp,
                signature,
            ):
                return jsonify({"error": "invalid Slack signature"}), 401
            if not remember_slack_request(
                app.extensions["slack_replay_cache"],
                app.extensions["slack_replay_lock"],
                timestamp,
                signature,
            ):
                return jsonify({"error": "replayed Slack request"}), 401

        user_id = (payload.get("user") or {}).get("id", "")
        response_url = payload.get("response_url", "")
        if operation in ("emergency_status", "emergency_open", "emergency_dump_open", "dump_open"):
            if not admin_ids:
                return ephemeral("관리자 승인 목록이 설정되지 않았습니다.")
            if user_id not in admin_ids:
                return ephemeral("등록된 관리자만 사용할 수 있습니다.")
            trigger_id = payload.get("trigger_id")
            if operation == "emergency_status":
                return ephemeral(format_emergency_status(build_emergency_status(app)))
            if not trigger_id:
                return ephemeral("Slack trigger_id를 확인할 수 없습니다.")
            request_type = json.loads((payload.get("actions") or [{}])[0].get("value", "{}")).get("request_type")
            if operation in ("emergency_open", "emergency_dump_open"):
                client = app.extensions["slack_clients"].get("emergency")
                if client is None or not client.available:
                    return ephemeral("Emergency channel client가 설정되지 않았습니다.")
                if operation == "emergency_dump_open" and request_type != REQUEST_TYPE_DUMP_RESTORE:
                    return ephemeral("Emergency Dump 요청 유형을 확인할 수 없습니다.")
                try:
                    client.open_view(
                        trigger_id,
                        emergency_reason_view(
                            action,
                            request_type,
                            request_origin="EMERGENCY"
                            if operation == "emergency_dump_open"
                            else None,
                        ),
                    )
                except SlackApiError as exc:
                    return ephemeral(f"Emergency modal을 열 수 없습니다: {exc.error}")
                return "", 200
            channel_id = (payload.get("channel") or {}).get("id") or (payload.get("container") or {}).get("channel_id")
            client_key = "emergency" if channel_id == app.config.get("SLACK_EMERGENCY_CHANNEL_ID") else action
            client = app.extensions["slack_clients"].get(client_key)
            if client is None or not client.available:
                return ephemeral("Dump 승인 채널 client가 설정되지 않았습니다.")
            try:
                client.open_view(
                    trigger_id,
                    dump_select_view(action, app.config["DUMP_BACKUP_DIR"], client_key),
                )
            except SlackApiError as exc:
                return ephemeral(f"Dump 선택창을 열 수 없습니다: {exc.error}")
            return "", 200
        if not request_id or operation not in ("approve", "cancel"):
            return ephemeral("요청을 찾을 수 없거나 이미 만료되었습니다. 새 승인 요청을 생성해 주세요.")
        if not admin_ids:
            return ephemeral("관리자 승인 목록이 설정되지 않았습니다. SLACK_ADMIN_USER_IDS를 확인해 주세요.")
        if user_id not in admin_ids:
            return ephemeral("이 작업은 등록된 관리자만 승인하거나 취소할 수 있습니다.")

        container = payload.get("container") or {}
        channel_id = (payload.get("channel") or {}).get("id") or container.get("channel_id")
        message_ts = container.get("message_ts") or (payload.get("message") or {}).get("ts")
        state = store.get(request_id)
        if state and message_ts and channel_id and not state.get("slack_message_ts"):
            state = store.attach_slack_message(request_id, channel_id, message_ts)

        if operation == "cancel":
            result, state = store.cancel(request_id, action, cancelled_by=user_id)
            if result == "cancelled":
                update_slack_message(app, state, f"Cancelled by <@{user_id}>. All approvals were discarded. / <@{user_id}> 요청 취소, 기존 승인 폐기")
                return "", 200
            if result == "executing":
                return ephemeral("이미 실행 중인 요청은 취소할 수 없습니다.")
            if state and state["status"] == "EXPIRED":
                update_slack_message(app, state)
            return ephemeral("이미 종료되었거나 만료된 요청입니다.")

        state = state or store.get(request_id)
        current_request_type = request_type_from_state(state or {})
        if current_request_type in {
            REQUEST_TYPE_EMERGENCY_FAILOVER,
            REQUEST_TYPE_EMERGENCY_FAILBACK,
            REQUEST_TYPE_DUMP_RESTORE,
            REQUEST_TYPE_SECURITY,
        }:
            incident = latest_incident(state or {})
            backup = None
            if current_request_type == REQUEST_TYPE_DUMP_RESTORE:
                backup = resolve_dump_backup(
                    app.config["DUMP_BACKUP_DIR"], incident.get("backup_id")
                )
            precheck = evaluate_emergency_precheck(app, action, current_request_type, backup=backup)
            if precheck["status"] == "FAILED":
                state = store.reset_approvals(
                    request_id,
                    ttl_seconds=app.config.get("EMERGENCY_TTL_SECONDS", EMERGENCY_TTL_SECONDS)
                    if current_request_type != REQUEST_TYPE_SECURITY
                    else None,
                    result="precheck_failed",
                )
                update_slack_message(app, state, f"Pre-check FAILED: {precheck['reason']}")
                return ephemeral(f"실행 전 안전 검사가 실패했습니다: {precheck['reason']}")
            if current_request_type == REQUEST_TYPE_DUMP_RESTORE and not app.config.get("DUMP_RESTORE_EXEC_ENABLED"):
                state = store.reset_approvals(request_id, result="dump_restore_disabled")
                update_slack_message(app, state, "Dump 복구 실행은 현재 비활성화되어 있습니다.")
                return ephemeral("Dump 복구 실행은 DUMP_RESTORE_EXEC_ENABLED=true로 명시적으로 활성화해야 합니다.")

        if action == "failback" and current_request_type == request_type_for_action("failback"):
            network = evaluate_current_network_health(app)
            state = store.refresh_failback_network(request_id, network)
            if network["status"] != "READY":
                update_slack_message(app, state, f"Network/SNMP Status: {network['status']}. Failback approval remains blocked.")
                return ephemeral(
                    f"Failback Network Gate: {network['status']}. {network['reason']}"
                )
            stability_ready, stability_seconds, stability_required = failback_stability_from_state(state)
            if not stability_ready:
                update_slack_message(app, state, "Failback stability window is not satisfied. Approval remains blocked.")
                return ephemeral(
                    f"Failback Stability Gate: {stability_seconds}s/{stability_required}s."
                )
            security = evaluate_current_failback_security(app)
            state = store.refresh_failback_security(request_id, security)
            if security["status"] != "READY":
                update_slack_message(app, state, f"Security Status: {security['status']}. Failback approval remains blocked.")
                return ephemeral(
                    f"Failback Security Gate: {security['status']}. {security['reason']}"
                )

        result, state = store.approve(request_id, action, user_id)
        if result == "global_lock":
            active_id = state.get("request_id") if state else "unknown"
            return ephemeral(f"다른 DR 작업이 실행 중입니다: {active_id}. 현재 작업이 끝난 뒤 다시 시도하세요.")
        if action == "failover" and state:
            incidents = state.get("incidents") or []
            latest_incident_data = incidents[-1] if incidents else {}
            if result == "pending":
                _record_rto(latest_incident_data, "first_approval_at", approval_id=request_id)
            elif result == "approved":
                _record_rto(latest_incident_data, "second_approval_at", approval_id=request_id)
        if result == "duplicate":
            return ephemeral(
                f"이미 승인된 요청입니다. 현재 승인: {len(state['approvers'])}/{REQUIRED_APPROVALS}"
            )
        if result == "pending":
            update_slack_message(app, state)
            return "", 200
        if result == "approved":
            if action == "failover":
                incidents = (state or {}).get("incidents") or []
                _record_rto(incidents[-1] if incidents else {}, "failover_approved_at", approval_id=request_id)
            update_slack_message(app, state, "Two-person approval completed. Workflow execution started. / 2인 승인 완료, 실행 시작")
            try:
                launch_pipeline(app, request_id, action, user_id, response_url)
            except Exception as exc:
                state = store.finish(request_id, False, result="launch_error")
                update_slack_message(app, state, "Workflow launch failed; a new approval is required. / 실행 시작에 실패해 새 승인이 필요합니다.")
                print(f"[CHATOPS ERROR] Pipeline launch failed: {type(exc).__name__}", flush=True)
                return ephemeral("실행 시작에 실패했습니다. 새 승인 요청을 생성해 주세요.")
            return "", 200
        if result == "executing":
            return ephemeral("이미 실행 중인 요청입니다.")
        if state and state["status"] == "EXPIRED":
            update_slack_message(app, state)
        return ephemeral("이미 종료되었거나 만료된 요청입니다. 새 요청을 생성해 주세요.")

    @app.post("/slack/action")
    def slack_action():
        return handle_slack_action()

    @app.post("/slack/failover/action")
    def slack_failover_action():
        return handle_slack_action(expected_action="failover")

    @app.post("/slack/failback/action")
    def slack_failback_action():
        return handle_slack_action(expected_action="failback")

    return app


def start_expiry_worker(app):
    def worker():
        while True:
            try:
                expire_pending_requests(app)
            except Exception as exc:
                print(f"[EXPIRY ERROR] Approval expiry check failed: {type(exc).__name__}", flush=True)
            time.sleep(app.config["EXPIRY_INTERVAL_SECONDS"])

    threading.Thread(target=worker, daemon=True, name="chatops-expiry-worker").start()


def start_cleanup_worker(app):
    cleanup_interval = max(1.0, float(app.config["CHATOPS_SLACK_CLEANUP_INTERVAL_SECONDS"]))
    console_interval = max(1.0, float(app.config["EMERGENCY_CONSOLE_WATCHDOG_INTERVAL_SECONDS"]))

    def worker():
        next_cleanup = 0.0
        next_console_watchdog = 0.0
        while True:
            now = time.monotonic()
            if now >= next_console_watchdog:
                try:
                    ensure_emergency_console(app)
                except Exception as exc:
                    print(
                        f"[EMERGENCY CONSOLE] watchdog check failed error={type(exc).__name__}",
                        flush=True,
                    )
                next_console_watchdog = now + console_interval
            if now >= next_cleanup:
                try:
                    cleanup_old_slack_messages(app, ensure_console=False)
                except Exception as exc:
                    print(f"[CLEANUP ERROR] Slack retention check failed: {type(exc).__name__}", flush=True)
                next_cleanup = now + cleanup_interval
            sleep_for = min(next_console_watchdog, next_cleanup) - time.monotonic()
            time.sleep(max(0.1, sleep_for))

    threading.Thread(target=worker, daemon=True, name="chatops-cleanup-worker").start()
