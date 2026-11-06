import json
import sys
import tempfile
import unittest
from pathlib import Path


CHATOPS_DIR = Path(__file__).resolve().parents[1] / "operations" / "chatops"
sys.path.insert(0, str(CHATOPS_DIR))

from chatops_core import create_app
from wazuh_correlation import CorrelationState, correlate_signals, normalize_signal


class FakeSlackClient:
    channel_id = "C_SECURITY_TEST"

    @property
    def available(self):
        return True

    def __init__(self):
        self.posts = []

    def post_message(self, text, blocks):
        self.posts.append((text, blocks))
        return {"ok": True, "channel": self.channel_id, "ts": f"9000.{len(self.posts)}"}


def signal(rule_id, groups, description, location=""):
    return {
        "id": f"event-{rule_id}-{description}",
        "agent": {"id": "001", "name": "hospital-web"},
        "rule": {"id": str(rule_id), "level": 8, "description": description, "groups": groups},
        "location": location,
    }


class WazuhCorrelationTest(unittest.TestCase):
    def test_auth_and_fim_creates_one_high_incident_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            state = CorrelationState(str(Path(directory) / "correlation.json"), window_seconds=900)
            first = state.record(signal("5710", ["authentication_failed"], "Invalid user"), now=1000)
            self.assertEqual(first, [])
            second = state.record(
                signal("5502", ["syscheck"], "File modified", "/etc/nginx/nginx.conf"), now=1010
            )
            self.assertEqual(len(second), 1)
            self.assertEqual(second[0]["scenario"], "AUTH_AND_FIM")
            duplicate = state.record(
                signal("5502", ["syscheck"], "File modified", "/etc/nginx/nginx.conf"), now=1011
            )
            self.assertEqual(duplicate, [])

    def test_old_signals_are_outside_the_correlation_window(self):
        old = normalize_signal(signal("5710", ["authentication_failed"], "Invalid user"), now=0)
        recent = normalize_signal(signal("5502", ["syscheck"], "File modified", "/etc/nginx/nginx.conf"), now=1000)
        self.assertEqual(correlate_signals([old, recent], window_seconds=900, now=1000), [])

    def test_chatops_emits_dedicated_security_message_for_correlation(self):
        with tempfile.TemporaryDirectory() as directory:
            state_db = str(Path(directory) / "state.sqlite3")
            security = FakeSlackClient()
            app = create_app(
                state_db,
                str(Path(directory) / "missing.env"),
                "/nonexistent",
                {
                    "TESTING": True,
                    "CHATOPS_STATE_DB": state_db,
                    "SLACK_ALERT_CLIENT": FakeSlackClient(),
                    "SLACK_CLIENTS": {"failover": FakeSlackClient(), "failback": FakeSlackClient()},
                    "ADMIN_USER_IDS": {"U1", "U2"},
                    "VERIFY_SLACK_SIGNATURE": False,
                    "WAZUH_CORRELATION_STATE_FILE": str(Path(directory) / "correlation.json"),
                    "WAZUH_SECURITY_SLACK_CLIENT": security,
                    "PIPELINE_LAUNCHER": lambda *args: None,
                },
            )
            client = app.test_client()
            for payload in (
                signal("5710", ["authentication_failed"], "Invalid user"),
                signal("5502", ["syscheck"], "File modified", "/etc/nginx/nginx.conf"),
                signal("5502", ["syscheck"], "File modified", "/etc/nginx/nginx.conf"),
            ):
                response = client.post("/wazuh/alert", json=payload, environ_base={"REMOTE_ADDR": "127.0.0.1"})
                self.assertEqual(response.status_code, 200)
            self.assertEqual(len(security.posts), 1)
            self.assertIn("[WAZUH SECURITY INCIDENT]", security.posts[0][0])

    def test_chatops_security_dry_run_builds_payload_without_sending(self):
        with tempfile.TemporaryDirectory() as directory:
            security = FakeSlackClient()
            app = create_app(
                str(Path(directory) / "state.sqlite3"),
                str(Path(directory) / "missing.env"),
                "/nonexistent",
                {
                    "TESTING": True,
                    "CHATOPS_STATE_DB": str(Path(directory) / "state.sqlite3"),
                    "SLACK_ALERT_CLIENT": FakeSlackClient(),
                    "SLACK_CLIENTS": {"failover": FakeSlackClient(), "failback": FakeSlackClient()},
                    "ADMIN_USER_IDS": {"U1", "U2"},
                    "VERIFY_SLACK_SIGNATURE": False,
                    "WAZUH_CORRELATION_STATE_FILE": str(Path(directory) / "correlation.json"),
                    "WAZUH_SECURITY_SLACK_CLIENT": security,
                    "WAZUH_SECURITY_SLACK_DRY_RUN": True,
                    "PIPELINE_LAUNCHER": lambda *args: None,
                },
            )
            client = app.test_client()
            for payload in (
                signal("5710", ["authentication_failed"], "Invalid user"),
                signal("5502", ["syscheck"], "File modified", "/etc/nginx/nginx.conf"),
            ):
                response = client.post("/wazuh/alert", json=payload, environ_base={"REMOTE_ADDR": "127.0.0.1"})
                self.assertEqual(response.status_code, 200)
            result = response.get_json()["security_correlation"]
            self.assertEqual(result["status"], "INCIDENT")
            self.assertTrue(result["dry_run"])
            self.assertTrue(result["payload_ready"])
            self.assertEqual(len(security.posts), 0)


if __name__ == "__main__":
    unittest.main()
