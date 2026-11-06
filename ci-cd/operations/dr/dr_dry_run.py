#!/usr/bin/env python3
"""Run a no-Slack, no-AWS DR approval and RTO contract dry-run.

This exercises the real ChatOps request/approval construction with in-memory
Slack clients.  It intentionally does not call Zabbix, Slack, AWS, DNS, or a
workflow executor.  The separate controlled Zabbix runner remains the test
for the live Zabbix Action path.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHATOPS_DIR = ROOT / "operations" / "chatops"
DB_DIR = ROOT / "operations" / "db"
sys.path.insert(0, str(CHATOPS_DIR))

from chatops_core import create_app  # noqa: E402
from rto_timeline import metric_values, synthetic_run  # noqa: E402


class DryRunSlackClient:
    def __init__(self, channel_id: str):
        self.channel_id = channel_id
        self.posts: list[dict[str, object]] = []

    @property
    def available(self) -> bool:
        return True

    def post_message(self, text, blocks):
        self.posts.append({"text": text, "blocks": blocks})
        return {"ok": True, "channel": self.channel_id, "ts": f"dry-run.{len(self.posts)}"}

    def update_message(self, channel_id, message_ts, text, blocks):
        return {"ok": True, "channel": channel_id, "ts": message_ts}

    def delete_message(self, channel_id, message_ts):
        return {"ok": True}


def _healthy_wazuh_snapshot():
    return {
        "query_ok": True,
        "agents": [
            {"id": "001", "name": "DB_Server", "status": "active", "last_seen": 4102444800},
            {"id": "002", "name": "Web_Server", "status": "active", "last_seen": 4102444800},
            {"id": "003", "name": "EMR_Server", "status": "active", "last_seen": 4102444800},
        ],
        "incidents": [],
    }


def _stability_contract_passes() -> bool:
    helper = DB_DIR / "failback_stability.sh"
    command = (
        f"source {helper!s}; "
        "failback_stability_validate_config 300 2 && "
        "! failback_stability_is_satisfied 100 399 300 && "
        "failback_stability_is_satisfied 100 400 300"
    )
    result = subprocess.run(["bash", "-c", command], cwd=ROOT, check=False)
    return result.returncode == 0


def _healthy_network_snapshot():
    return {
        "status": "READY",
        "reason": "all required SNMP availability items are fresh and ready",
        "source": "TEST_FIXTURE",
        "checked_at": "2026-09-19T00:00:00Z",
        "devices": {
            name: {"status": "READY", "value": "1"}
            for name in (
                "Cisco-2800-Series",
                "Catalyst-2950-Series",
                "Catalyst-3560-Series",
                "Catalyst-3550-Series",
            )
        },
    }


def run() -> int:
    with tempfile.TemporaryDirectory(prefix="hospital-dr-dry-run-") as temporary:
        temp = Path(temporary)
        state_db = temp / "chatops.sqlite3"
        timeline = temp / "rto_timeline.json"
        environment = {
            "DR_RTO_TIMELINE_FILE": str(timeline),
            "DR_STATE_FILE": str(temp / "dr_state"),
        }
        import os

        os.environ.update(environment)
        failover = DryRunSlackClient("C_DRY_FAILOVER")
        failback_client = DryRunSlackClient("C_DRY_FAILBACK")
        alert = DryRunSlackClient("C_DRY_ALERT")
        app = create_app(
            default_state_db=str(state_db),
            default_secret_file=str(temp / "missing.env"),
            default_script_root=str(ROOT / "operations"),
            test_config={
                "TESTING": True,
                "CHATOPS_STATE_DB": str(state_db),
                "SLACK_CLIENTS": {"failover": failover, "failback": failback_client},
                "SLACK_ALERT_CLIENT": alert,
                "ADMIN_USER_IDS": {"U_DRY_1", "U_DRY_2"},
                "WAZUH_FAILBACK_REQUIRED_AGENTS": ("DB_Server", "Web_Server", "EMR_Server"),
                "WAZUH_FAILBACK_SECURITY_SNAPSHOT": _healthy_wazuh_snapshot(),
                "NETWORK_DR_HEALTH_SNAPSHOT": _healthy_network_snapshot(),
                "VERIFY_SLACK_SIGNATURE": False,
                "PIPELINE_LAUNCHER": lambda request_id, action, approver: None,
            },
        )
        client = app.test_client()
        event_id = "dry-run-event-1"
        incident_at = "2026-09-19T00:00:00Z"
        problem = client.post(
            "/zabbix/alert",
            json={
                "EventID": event_id,
                "Host": "DR-Dry-Run",
                "Status": "PROBLEM",
                "Value": "1",
                "Severity": "HIGH",
                "Message": "controlled dry-run problem",
                "Timestamp": incident_at,
                "DR_Escalate": "1",
            },
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        problem_result = problem.get_json() or {}
        problem_pass = problem.status_code == 200 and problem_result.get("dr_escalated") is True

        recovery = client.post(
            "/zabbix/alert",
            json={
                "EventID": event_id,
                "Host": "DR-Dry-Run",
                "Status": "RESOLVED",
                "Value": "0",
                "Severity": "HIGH",
                "Message": "controlled dry-run recovery",
                "Timestamp": incident_at,
            },
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        recovery_pass = recovery.status_code == 200 and recovery.get_json().get("alert_sent") is True

        failback_response = client.post(
            "/chatops/request",
            json={
                "source": "zabbix",
                "action": "failback",
                "incident_id": "dry-run-recovery-1",
                "summary": "controlled dry-run recovery",
                "host": "DR-Dry-Run",
                "event_age_seconds": "0",
                "incident_at": incident_at,
                "test_mode": True,
                "stability_verified": True,
                "stability_seconds": 300,
            },
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        failback_result = failback_response.get_json() or {}
        failback_pass = failback_response.status_code == 200 and failback_result.get("action") == "failback"

        timeline_result = synthetic_run(timeline, "dry-run-rto")
        timeline_metrics = metric_values(timeline_result)
        dashboard_paths = (
            ROOT / "monitoring/grafana/provisioning/dashboards/json/hospital-dr-automation.json",
            ROOT / "monitoring/grafana/provisioning/dashboards/json/hospital-dr-rto.json",
        )
        dashboards = [json.loads(path.read_text(encoding="utf-8")) for path in dashboard_paths]
        serialized_dashboards = json.dumps(dashboards, ensure_ascii=True)
        required_rto_keys = (
            "dr.rto.seconds",
            "dr.rto.target_seconds",
            "dr.rto.result",
            "dr.rto.detection_seconds",
            "dr.rto.approval_creation_seconds",
            "dr.rto.approval_seconds",
            "dr.rto.approval_to_start_seconds",
            "dr.rto.execution_seconds",
        )
        grafana_pass = all(key in serialized_dashboards for key in required_rto_keys) and (
            "RPO STATUS: NR NOT READY" in serialized_dashboards
        )
        stability_pass = _stability_contract_passes()

        results = {
            "PROBLEM_EVENT": "PASS" if problem_pass else "FAIL",
            "GENERAL_ALERT": "PASS" if len(alert.posts) >= 2 else "FAIL",
            "FAILOVER_APPROVAL": "PASS" if len(failover.posts) == 1 else "FAIL",
            "FAILOVER_EXECUTION": "SKIPPED_DRY_RUN",
            "RECOVERY_EVENT": "PASS" if recovery_pass else "FAIL",
            "NETWORK_DR_HEALTH": "PASS"
            if failback_result.get("action") == "failback"
            and failback_result.get("status") == "PENDING"
            else "FAIL",
            "RECOVERY_STABILITY_GUARD": "PASS" if stability_pass else "FAIL",
            "FAILBACK_APPROVAL": "PASS" if failback_pass and len(failback_client.posts) == 1 else "FAIL",
            "RTO_METRICS": "PASS"
            if timeline_result["total_rto_seconds"] == 600.0
            and timeline_metrics.get("dr.rto.seconds") == 600.0
            and timeline_metrics.get("dr.rto.result") == 1
            else "FAIL",
            "GRAFANA_RTO_DATA": "PASS" if grafana_pass else "FAIL",
            "FAILBACK_EXECUTION": "SKIPPED_DRY_RUN",
            "DR_DRY_RUN_RESULT": "PASS",
            "RTO": timeline_result,
        }
        if any(value == "FAIL" for key, value in results.items() if key != "RTO"):
            results["DR_DRY_RUN_RESULT"] = "FAIL"
        for key in (
            "PROBLEM_EVENT",
            "GENERAL_ALERT",
            "FAILOVER_APPROVAL",
            "FAILOVER_EXECUTION",
            "RECOVERY_EVENT",
            "NETWORK_DR_HEALTH",
            "RECOVERY_STABILITY_GUARD",
            "FAILBACK_APPROVAL",
            "FAILBACK_EXECUTION",
            "RTO_METRICS",
            "GRAFANA_RTO_DATA",
            "DR_DRY_RUN_RESULT",
        ):
            print(f"{key}={results[key]}")
        print("RTO_JSON=" + json.dumps(timeline_result, ensure_ascii=True, sort_keys=True))
        return 0 if results["DR_DRY_RUN_RESULT"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(run())
