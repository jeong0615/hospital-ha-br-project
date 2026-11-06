import json
import stat
import tempfile
import unittest
from pathlib import Path


import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations" / "chatops"))

from ngrok_url_check import check_url, fetch_public_urls, write_expected  # noqa: E402


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class NgrokUrlCheckTests(unittest.TestCase):
    def test_unit_records_dynamic_url_baseline_after_startup(self):
        unit = (ROOT / "operations/chatops/systemd/hospital-dr-chatops-ngrok.service").read_text()
        self.assertIn("Environment=NGROK_URL_STATE_FILE=/var/lib/hospital-dr/ngrok_public_url", unit)
        self.assertIn("StateDirectory=hospital-dr", unit)
        self.assertIn("ExecStartPost=/usr/bin/python3 /home/cloud/dr_project/ngrok_url_check.py --record --wait-seconds 30", unit)
        self.assertNotIn(" --url ", unit)

    def test_fetches_one_valid_public_url(self):
        urls = fetch_public_urls(
            opener=lambda request, timeout: Response({"tunnels": [{"public_url": "https://stable.example"}]})
        )
        self.assertEqual(urls, ["https://stable.example"])

    def test_changed_or_missing_baseline_fails_check(self):
        self.assertEqual(check_url(["https://new.example"], None)[0], False)
        self.assertEqual(check_url(["https://new.example"], "https://old.example")[0], False)
        self.assertEqual(check_url(["https://same.example"], "https://same.example")[0], True)

    def test_state_write_is_atomic_and_restricted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "url"
            write_expected(path, "https://stable.example")
            self.assertEqual(path.read_text(), "https://stable.example\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o750)


if __name__ == "__main__":
    unittest.main()
