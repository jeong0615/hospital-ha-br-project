import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations" / "monitoring"))

from grafana_panel_query_check import _numeric_values, check_dashboard  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append((url, json, timeout))
        return FakeResponse(self.payload)


class GrafanaPanelQueryCheckTest(unittest.TestCase):
    def test_numeric_values_reads_grafana_frame_data(self):
        frame = {"data": {"values": [[1, 2], [0, 3]]}}
        self.assertEqual(_numeric_values(frame), [0.0, 3.0])

    def test_explicit_state_is_pass_without_query(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "dashboard.json"
            path.write_text(
                json.dumps(
                    {
                        "time": {"from": "now-1h", "to": "now"},
                        "panels": [
                            {
                                "id": 1,
                                "type": "text",
                                "title": "WG status",
                                "options": {"content": "POWERED_OFF_OR_NOT_AVAILABLE", "mode": "markdown"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = check_dashboard(FakeSession({}), "http://grafana", path, None, "now")
        self.assertEqual(result[0]["status"], "PASS")
        self.assertEqual(result[0]["kind"], "explicit-state")

    def test_query_frame_is_pass(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "dashboard.json"
            path.write_text(
                json.dumps(
                    {
                        "time": {"from": "now-1h", "to": "now"},
                        "panels": [
                            {
                                "id": 1,
                                "type": "stat",
                                "title": "Real value",
                                "datasource": {"uid": "test"},
                                "targets": [{"refId": "A"}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            session = FakeSession({"results": {"A": {"frames": [{"data": {"values": [[1], [7]]}}]}}})
            result = check_dashboard(session, "http://grafana", path, None, "now")
        self.assertEqual(result[0]["status"], "PASS")
        self.assertIn("last=7", result[0]["detail"])
        self.assertEqual(session.calls[0][2], 45)


if __name__ == "__main__":
    unittest.main()
