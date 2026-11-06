import json
import sys
import tempfile
import time
import unittest
from pathlib import Path


CHATOPS_DIR = Path(__file__).resolve().parents[1] / "operations" / "chatops"
sys.path.insert(0, str(CHATOPS_DIR))

from chatops_core import button_value, create_app  # noqa: E402
from network_dr_health import DEFAULT_REQUIRED_HOSTS  # noqa: E402


class FakeSlackClient:
    def __init__(self):
        self.posts = []
        self.updates = []
        self.channel_id = "C_NETWORK_GATE"

    @property
    def available(self):
        return True

    def post_message(self, text, blocks):
        self.posts.append({"text": text, "blocks": blocks})
        return {"ok": True, "channel": self.channel_id, "ts": f"5000.{len(self.posts)}"}

    def update_message(self, channel_id, message_ts, text, blocks):
        self.updates.append({"channel": channel_id, "ts": message_ts, "text": text, "blocks": blocks})
        return {"ok": True}


def network_snapshot(status="READY"):
    return {
        "status": status,
        "reason": "fixture network result",
        "source": "TEST_FIXTURE",
        "checked_at": "2026-09-29T00:00:00Z",
        "devices": {name: {"status": status} for name in DEFAULT_REQUIRED_HOSTS},
    }


def wazuh_snapshot():
    now = time.time()
    return {
        "query_ok": True,
        "agents": [
            {"id": "001", "name": "DB_Server", "status": "active", "last_seen": now},
            {"id": "002", "name": "Web_Server", "status": "active", "last_seen": now},
            {"id": "003", "name": "EMR_Server", "status": "active", "last_seen": now},
        ],
        "incidents": [],
    }


class ChatOpsNetworkGateTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.slack = FakeSlackClient()
        state_db = str(Path(self.temp_dir.name) / "state.sqlite3")
        dr_state_file = Path(self.temp_dir.name) / "dr_state"
        dr_state_file.write_text("DR_ACTIVE\n", encoding="utf-8")
        self.app = create_app(
            default_state_db=state_db,
            default_secret_file=str(Path(self.temp_dir.name) / "missing.env"),
            default_script_root="/nonexistent/mock-only",
            test_config={
                "TESTING": False,
                "CHATOPS_STATE_DB": state_db,
                "DR_STATE_FILE": str(dr_state_file),
                "SLACK_CLIENTS": {"failover": self.slack, "failback": self.slack},
                "ADMIN_USER_IDS": {"U_ADMIN_1", "U_ADMIN_2"},
                "WAZUH_FAILBACK_REQUIRED_AGENTS": ("DB_Server", "Web_Server", "EMR_Server"),
                "WAZUH_FAILBACK_SECURITY_SNAPSHOT": wazuh_snapshot(),
                "NETWORK_DR_HEALTH_SNAPSHOT": network_snapshot(),
                "VERIFY_SLACK_SIGNATURE": False,
                "PIPELINE_LAUNCHER": lambda *_args: None,
            },
        )
        self.client = self.app.test_client()
        self.store = self.app.extensions["approval_store"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def request(self, stability_seconds=300):
        return self.client.post(
            "/chatops/request",
            json={
                "source": "zabbix",
                "action": "failback",
                "incident_id": "network-gate-event",
                "summary": "network gate fixture",
                "event_age_seconds": 5,
                "stability_verified": True,
                "stability_seconds": stability_seconds,
            },
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

    def interact(self, request_id, operation, user):
        payload = {
            "actions": [{"value": button_value(request_id, "failback", operation)}],
            "user": {"id": user},
            "channel": {"id": self.slack.channel_id},
            "container": {"message_ts": "5000.1", "channel_id": self.slack.channel_id},
        }
        return self.client.post("/slack/failback/action", data={"payload": json.dumps(payload)})

    def test_network_not_ready_blocks_request_before_slack(self):
        self.app.config["NETWORK_DR_HEALTH_SNAPSHOT"] = network_snapshot("NOT_READY")
        response = self.request()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.slack.posts, [])

    def test_stability_boundary_is_fail_closed_before_network_request(self):
        response = self.request(stability_seconds=299)
        self.assertEqual(response.status_code, 409)
        self.assertIn("stability window", response.get_json()["error"])
        self.assertEqual(self.slack.posts, [])

        response = self.request(stability_seconds=300)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.slack.posts), 1)

    def test_ready_network_is_displayed_and_regression_resets_partial_approval(self):
        response = self.request()
        self.assertEqual(response.status_code, 200)
        request_id = response.get_json()["request_id"]
        text = self.slack.posts[-1]["blocks"][0]["text"]["text"]
        self.assertIn("Network/SNMP: *READY*", text)
        self.assertIn("Stability: *300s/300s PASS*", text)

        self.assertEqual(self.interact(request_id, "approve", "U_ADMIN_1").status_code, 200)
        self.assertEqual(self.store.get(request_id)["approvers"], ["U_ADMIN_1"])

        self.app.config["NETWORK_DR_HEALTH_SNAPSHOT"] = network_snapshot("NOT_READY")
        blocked = self.interact(request_id, "approve", "U_ADMIN_2")
        self.assertEqual(blocked.status_code, 200)
        self.assertIn("Network Gate", blocked.get_json()["text"])
        state = self.store.get(request_id)
        self.assertEqual(state["status"], "PENDING")
        self.assertEqual(state["approvers"], [])


if __name__ == "__main__":
    unittest.main()
