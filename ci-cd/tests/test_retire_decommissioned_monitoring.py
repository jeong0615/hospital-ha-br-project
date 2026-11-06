import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations" / "zabbix"))

from configure_chatops import ConfigurationError  # noqa: E402
from retire_decommissioned_monitoring import (  # noqa: E402
    apply_aws_hosts,
    apply_test_host,
    discover_aws_hosts,
    discover_test_host,
)


class FakeApi:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "host.delete":
            return params
        return self.responses.get(method, [])


class RetireDecommissionedMonitoringTest(unittest.TestCase):
    def test_test_host_absent_is_idempotent(self):
        api = FakeApi({"host.get": []})
        discovery = discover_test_host(api)
        self.assertEqual(discovery["status"], "ABSENT")
        apply_test_host(api, discovery)
        self.assertFalse(any(method == "host.delete" for method, _ in api.calls))

    def test_test_host_with_non_test_trigger_is_blocked(self):
        api = FakeApi(
            {
                "host.get": [
                    {"hostid": "10662", "name": "DR-CHATOPS-TEST", "groups": [], "tags": []}
                ],
                "trigger.get": [{"triggerid": "1", "description": "Production trigger"}],
            }
        )
        discovery = discover_test_host(api)
        self.assertEqual(discovery["status"], "BLOCKED")
        with self.assertRaises(ConfigurationError):
            apply_test_host(api, discovery)
        self.assertFalse(any(method == "host.delete" for method, _ in api.calls))

    def test_test_host_deletes_only_exact_host(self):
        api = FakeApi(
            {
                "host.get": [
                    {"hostid": "10662", "name": "DR-CHATOPS-TEST", "groups": [], "tags": []}
                ],
                "trigger.get": [{"triggerid": "1", "description": "DR TEST: old"}],
            }
        )
        discovery = discover_test_host(api)
        apply_test_host(api, discovery)
        self.assertEqual(api.calls[-1], ("host.delete", ["10662"]))

    def test_aws_hosts_require_ownership_tags_and_group(self):
        api = FakeApi(
            {
                "host.get": [
                    {
                        "hostid": str(index),
                        "host": name,
                        "groups": [{"name": "AWS DR"}],
                        "tags": [
                            {"tag": "environment", "value": "aws"},
                            {"tag": "dr", "value": "true"},
                        ],
                    }
                    for index, name in enumerate(("aws-dr-web", "aws-dr-emr", "aws-dr-db"), start=1)
                ]
            }
        )
        discovery = discover_aws_hosts(api)
        apply_aws_hosts(api, discovery)
        self.assertEqual(api.calls[-1], ("host.delete", ["1", "2", "3"]))

    def test_aws_host_without_ownership_tag_is_blocked(self):
        api = FakeApi(
            {
                "host.get": [
                    {
                        "hostid": "1",
                        "host": "aws-dr-web",
                        "groups": [{"name": "AWS DR"}],
                        "tags": [{"tag": "environment", "value": "production"}],
                    }
                ]
            }
        )
        discovery = discover_aws_hosts(api)
        with self.assertRaises(ConfigurationError):
            apply_aws_hosts(api, discovery)
        self.assertFalse(any(method == "host.delete" for method, _ in api.calls))


if __name__ == "__main__":
    unittest.main()
