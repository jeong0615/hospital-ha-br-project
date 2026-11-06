"""Safe, side-effect-free helpers for the Emergency DR ChatOps surface.

This module intentionally does not execute a DR workflow, restore a database,
or call Slack.  It builds validated Block Kit views, persists the emergency
console coordinate, and inventories complete local backup pairs with
parseable replication metadata and an OpenSSL encrypted-container marker.  The
existing ChatOps approval engine remains the only approval/execution path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


EMERGENCY_TTL_SECONDS = 15 * 60
MAX_DUMP_OPTIONS = 20

REQUEST_TYPE_EMERGENCY_FAILOVER = "EMERGENCY_FAILOVER"
REQUEST_TYPE_EMERGENCY_FAILBACK = "EMERGENCY_FAILBACK"
REQUEST_TYPE_DUMP_RESTORE = "DUMP_RESTORE_FAILOVER"
REQUEST_TYPE_SECURITY = "SECURITY_DR_ESCALATION"

FAILOVER_REASONS = (
    ("REQUEST_NOT_CREATED", "요청 메시지가 생성되지 않았습니다."),
    ("REQUEST_EXPIRED", "요청 메시지가 만료되었습니다."),
    ("REQUEST_CANCELLED_ACCIDENTALLY", "요청 메시지를 실수로 취소했습니다."),
    ("IMMEDIATE_RECOVERY_REQUIRED", "즉시 복구가 필요하여 대기할 수 없습니다."),
    ("OTHER", "기타"),
)

DUMP_FAILOVER_REASONS = (
    *FAILOVER_REASONS[:-1],
    ("POINT_IN_TIME_DUMP_REQUIRED", "현재 NR 복제본 대신 특정 백업 시점 복구가 필요합니다."),
    FAILOVER_REASONS[-1],
)

FAILBACK_REASONS = (
    ("REQUEST_NOT_CREATED", "Failback 요청 메시지가 생성되지 않았습니다."),
    ("REQUEST_EXPIRED", "요청 메시지가 만료되었습니다."),
    ("REQUEST_CANCELLED_ACCIDENTALLY", "요청 메시지를 실수로 취소했습니다."),
    ("ONPREM_RECOVERED_IMMEDIATELY", "On-Prem 정상화가 확인되어 즉시 복귀가 필요합니다."),
    ("STATE_MISMATCH", "자동 상태 검증 결과와 실제 상태가 일치하지 않습니다."),
    ("OTHER", "기타"),
)


def _safe_text(value, limit=500):
    return " ".join(str(value or "").split())[:limit]


def request_type_for(action, emergency=False, dump_restore=False):
    if dump_restore:
        return REQUEST_TYPE_DUMP_RESTORE
    if emergency and action == "failover":
        return REQUEST_TYPE_EMERGENCY_FAILOVER
    if emergency and action == "failback":
        return REQUEST_TYPE_EMERGENCY_FAILBACK
    return "NORMAL_FAILOVER" if action == "failover" else "NORMAL_FAILBACK"


def reason_options(action, request_type=None):
    if request_type == REQUEST_TYPE_DUMP_RESTORE:
        return DUMP_FAILOVER_REASONS
    return FAILOVER_REASONS if action == "failover" else FAILBACK_REASONS


def validate_reason(action, reason_code, reason_text="", request_type=None):
    allowed = {code for code, _ in reason_options(action, request_type)}
    code = str(reason_code or "").strip()
    detail = _safe_text(reason_text, 500)
    if code not in allowed:
        return False, "사유를 반드시 선택해주십시오."
    if code == "OTHER" and not detail:
        return False, "기타 사유를 입력해주십시오."
    return True, detail


class EmergencyConsoleStore:
    """Persist exactly one emergency console coordinate in the ChatOps DB."""

    def __init__(self, state_db):
        self.state_db = state_db
        parent = os.path.dirname(os.path.abspath(state_db))
        os.makedirs(parent, mode=0o700, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.state_db, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS emergency_console (
                    console_id INTEGER PRIMARY KEY CHECK (console_id = 1),
                    channel_id TEXT NOT NULL,
                    message_ts TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_checked_at REAL NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )

    def get(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM emergency_console WHERE console_id = 1"
            ).fetchone()
        return dict(row) if row else None

    def save(self, channel_id, message_ts, created_at=None, status="READY", checked_at=None):
        now = time.time() if checked_at is None else float(checked_at)
        created_at = now if created_at is None else float(created_at)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO emergency_console
                    (console_id, channel_id, message_ts, created_at, last_checked_at, status)
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(console_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    message_ts = excluded.message_ts,
                    created_at = excluded.created_at,
                    last_checked_at = excluded.last_checked_at,
                    status = excluded.status
                """,
                (channel_id, message_ts, created_at, now, status),
            )
        return self.get()

    def mark_checked(self, status="READY", checked_at=None):
        now = time.time() if checked_at is None else float(checked_at)
        with self._connect() as connection:
            connection.execute(
                "UPDATE emergency_console SET last_checked_at = ?, status = ? WHERE console_id = 1",
                (now, status),
            )
        return self.get()

    def clear(self):
        with self._connect() as connection:
            connection.execute("DELETE FROM emergency_console WHERE console_id = 1")


def emergency_console_blocks(button_value):
    """Build the pinned console without approval controls."""
    text = (
        "*Hospital DR Emergency Console*\n\n"
        "자동 감지 또는 일반 ChatOps 요청 경로를 사용할 수 없는 경우에만 사용하십시오.\n"
        "긴급 요청도 관리자 2인 승인과 실행 전 안전 검사를 유지합니다."
    )
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "hospital_dr_emergency_status",
                    "text": {"type": "plain_text", "text": "상태 점검"},
                    "value": button_value("", "failover", "emergency_status"),
                },
                {
                    "type": "button",
                    "action_id": "hospital_dr_emergency_failover",
                    "text": {"type": "plain_text", "text": "긴급 Failover 요청"},
                    "style": "danger",
                    "value": button_value(
                        "", "failover", "emergency_open",
                        request_type=REQUEST_TYPE_EMERGENCY_FAILOVER,
                    ),
                },
                {
                    "type": "button",
                    "action_id": "hospital_dr_emergency_failback",
                    "text": {"type": "plain_text", "text": "긴급 Failback 요청"},
                    "style": "primary",
                    "value": button_value(
                        "", "failback", "emergency_open",
                        request_type=REQUEST_TYPE_EMERGENCY_FAILBACK,
                    ),
                },
                {
                    "type": "button",
                    "action_id": "hospital_dr_emergency_dump",
                    "text": {"type": "plain_text", "text": "긴급 Failover (Dump)"},
                    "value": button_value(
                        "", "failover", "emergency_dump_open",
                        request_type=REQUEST_TYPE_DUMP_RESTORE,
                    ),
                },
            ],
        },
    ]


def emergency_reason_view(action, request_type, request_origin=None):
    options = [
        {"text": {"type": "plain_text", "text": label}, "value": code}
        for code, label in reason_options(action, request_type)
    ]
    if request_type == REQUEST_TYPE_DUMP_RESTORE:
        title = "긴급 Failover (Dump) 요청"
        submit_text = "다음"
    else:
        title = "긴급 Failover 요청" if action == "failover" else "긴급 Failback 요청"
        submit_text = "요청 생성"
    return {
        "type": "modal",
        "callback_id": "hospital_dr_emergency_reason",
        "private_metadata": json.dumps(
            {
                "action": action,
                "request_type": request_type,
                **({"request_origin": request_origin} if request_origin else {}),
            },
            separators=(",", ":"),
        ),
        "title": {"type": "plain_text", "text": title},
        "submit": {"type": "plain_text", "text": submit_text},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": [
            {
                "type": "input",
                "block_id": "emergency_reason_code",
                "label": {"type": "plain_text", "text": "긴급 사유"},
                "element": {
                    "type": "static_select",
                    "action_id": "reason_code",
                    "placeholder": {"type": "plain_text", "text": "사유를 선택하세요"},
                    "options": options,
                },
            },
            {
                "type": "input",
                "block_id": "emergency_reason_detail",
                "optional": True,
                "label": {"type": "plain_text", "text": "상세 사유"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "reason_detail",
                    "multiline": True,
                    "max_length": 500,
                },
            },
        ],
    }


def _parse_backup_timestamp(name, mtime):
    match = re.search(r"db_backup_(\d{8}_(?:\d{4}|\d{6}))\.sql\.gz\.enc$", name)
    if match:
        try:
            format_string = "%Y%m%d_%H%M" if len(match.group(1).rsplit("_", 1)[1]) == 4 else "%Y%m%d_%H%M%S"
            parsed = datetime.strptime(match.group(1), format_string)
            return parsed.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return datetime.fromtimestamp(float(mtime), timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_master_status(status_path):
    try:
        text = status_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    file_match = re.search(r"\b(?:MASTER|SOURCE)_LOG_FILE\s*=\s*'([^']+)'", text, re.IGNORECASE)
    position_match = re.search(r"\b(?:MASTER|SOURCE)_LOG_POS\s*=\s*([0-9]+)", text, re.IGNORECASE)
    if file_match and position_match:
        file_name = file_match.group(1)
        position = int(position_match.group(1))
    else:
        file_match = re.search(r"^\s*(?:File|BinlogFile)\s*:\s*(\S+)\s*$", text, re.IGNORECASE | re.MULTILINE)
        position_match = re.search(r"^\s*(?:Position|BinlogPosition)\s*:\s*([0-9]+)\s*$", text, re.IGNORECASE | re.MULTILINE)
        if not file_match or not position_match:
            return None
        file_name = file_match.group(1)
        position = int(position_match.group(1))

    if not re.fullmatch(r"[A-Za-z0-9._-]+", file_name) or position < 0:
        return None

    timestamp_match = re.search(r"^\s*BackupTimestamp\s*:\s*(\S+)\s*$", text, re.IGNORECASE | re.MULTILINE)
    return {
        "backup_timestamp": _safe_text(timestamp_match.group(1), 80) if timestamp_match else "UNKNOWN",
        "master_status_file": file_name,
        "master_status_position": position,
    }


def _encrypted_container_status(path):
    try:
        with path.open("rb") as stream:
            header = stream.read(8)
        return "PASS" if header == b"Salted__" else "FAIL"
    except OSError:
        return "FAIL"


def list_dump_backups(directory, limit=MAX_DUMP_OPTIONS):
    """Return complete, readable encrypted backup pairs newest first."""
    root = Path(directory).expanduser()
    if not root.is_dir():
        return []
    results = []
    for path in root.glob("*.sql.gz.enc"):
        if path.is_symlink() or not path.is_file():
            continue
        status_path = path.with_name(path.name[: -len(".sql.gz.enc")] + ".master_status")
        try:
            stat = path.stat()
            status_stat = status_path.stat()
        except OSError:
            continue
        if not status_path.is_file() or status_path.is_symlink() or stat.st_size <= 0 or status_stat.st_size <= 0:
            continue
        status_metadata = _parse_master_status(status_path)
        if status_metadata is None or _encrypted_container_status(path) != "PASS":
            continue
        try:
            checksum = _sha256(path)
        except OSError:
            continue
        results.append(
            {
                "backup_id": path.name,
                "filename": path.name,
                "status_filename": status_path.name,
                "timestamp": _parse_backup_timestamp(path.name, stat.st_mtime),
                "backup_timestamp": status_metadata["backup_timestamp"],
                "size_bytes": stat.st_size,
                "checksum": checksum,
                "checksum_status": "COMPUTED",
                "encrypted": True,
                "encryption_status": "CONTAINER_MARKER_PASS",
                "integrity_status": "PAIR_METADATA_CONTAINER_PASS",
                "master_status_file": status_metadata["master_status_file"],
                "master_status_position": status_metadata["master_status_position"],
                "expected_rpo": "UNKNOWN_UNTIL_INCIDENT_TIMESTAMP",
                "path": str(path),
            }
        )
    results.sort(key=lambda item: (item["timestamp"], item["backup_id"]), reverse=True)
    return results[: max(1, int(limit))]


def resolve_dump_backup(directory, backup_id):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.sql\.gz\.enc", str(backup_id or "")):
        return None
    for item in list_dump_backups(directory, limit=MAX_DUMP_OPTIONS * 10):
        if item["backup_id"] == backup_id:
            return item
    return None


def dump_select_view(action, directory, slack_client_key=None, metadata=None):
    metadata = dict(metadata or {})
    metadata.update(
        {
            "action": action,
            "slack_client_key": slack_client_key or metadata.get("slack_client_key") or action,
        }
    )
    backups = list_dump_backups(directory)
    options = [
        {
            "text": {
                "type": "plain_text",
                "text": f"{item['timestamp']} | {item['size_bytes']} bytes | {item['checksum'][:12]}",
            },
            "value": item["backup_id"],
        }
        for item in backups
    ]
    if not options:
        options = [{"text": {"type": "plain_text", "text": "사용 가능한 complete dump 없음"}, "value": "NONE"}]
    return {
        "type": "modal",
        "callback_id": "hospital_dr_dump_select",
        "private_metadata": json.dumps(metadata, separators=(",", ":")),
        "title": {"type": "plain_text", "text": "Dump 복구 선택"},
        "submit": {"type": "plain_text", "text": "선택"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "실제 complete encrypted backup pair만 표시됩니다. 선택 후에도 즉시 복구하지 않습니다.",
                },
            },
            {
                "type": "input",
                "block_id": "dump_backup",
                "label": {"type": "plain_text", "text": "백업"},
                "element": {
                    "type": "static_select",
                    "action_id": "backup_id",
                    "placeholder": {"type": "plain_text", "text": "백업을 선택하세요"},
                    "options": options,
                },
            },
        ],
    }


def dump_confirm_view(action, backup, metadata=None):
    metadata = dict(metadata or {})
    metadata.update(
        {
            "action": action,
            "backup_id": backup["backup_id"],
            "checksum": backup["checksum"],
            "timestamp": backup["timestamp"],
        }
    )
    text = (
        f"*{backup['timestamp']}* 백업을 선택했습니다.\n"
        f"Backup ID: `{_safe_text(backup['backup_id'], 160)}`\n"
        f"Size: `{backup['size_bytes']}` bytes\n"
        f"Checksum: `{backup['checksum'][:16]}…`\n"
        f"Master status: `{_safe_text(backup['master_status_file'], 100)}:{backup['master_status_position']}`\n"
        f"Encryption: `{backup['encryption_status']}`\n"
        f"Integrity: `{backup['integrity_status']}`\n"
        f"Expected RPO: `{backup['expected_rpo']}`\n\n"
        "선택한 Dump 백업 파일로 Failover 승인 요청을 진행하시겠습니까?"
    )
    return {
        "type": "modal",
        "callback_id": "hospital_dr_dump_confirm",
        "private_metadata": json.dumps(metadata, separators=(",", ":")),
        "title": {"type": "plain_text", "text": "Dump 복구 확인"},
        "submit": {"type": "plain_text", "text": "제출"},
        "close": {"type": "plain_text", "text": "아니오"},
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {
                "type": "input",
                "block_id": "dump_confirm",
                "label": {"type": "plain_text", "text": "확인"},
                "element": {
                    "type": "radio_buttons",
                    "action_id": "confirm_choice",
                    "options": [
                        {"text": {"type": "plain_text", "text": "예"}, "value": "YES"},
                        {"text": {"type": "plain_text", "text": "아니오"}, "value": "NO"},
                    ],
                },
            },
        ],
    }
