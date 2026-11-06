import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHATOPS_DIR = ROOT / "operations" / "chatops"
sys.path.insert(0, str(CHATOPS_DIR))

from network_dr_health import (  # noqa: E402
    DEFAULT_REQUIRED_HOSTS,
    NOT_READY,
    READY,
    UNKNOWN,
    ZabbixNetworkHealthProvider,
    evaluate_network_snapshot,
)


def live_snapshot(now=None):
    now = int(time.time() if now is None else now)
    hosts = [
        {
            "hostid": str(index),
            "name": name,
            "interfaces": [{"type": "2", "available": "1"}],
        }
        for index, name in enumerate(DEFAULT_REQUIRED_HOSTS, start=1)
    ]
    items = [
        {
            "hostid": str(index),
            "host_name": name,
            "name": "Cisco IOS: SNMP agent availability",
            "key_": "zabbix[host,snmp,available]",
            "lastvalue": "1",
            "lastclock": str(now - 10),
            "state": "0",
            "status": "0",
        }
        for index, name in enumerate(DEFAULT_REQUIRED_HOSTS, start=1)
    ]
    return hosts, items


class NetworkDrHealthTest(unittest.TestCase):
    def test_all_required_devices_fresh_and_up_are_ready(self):
        hosts, items = live_snapshot(now=1000)
        result = evaluate_network_snapshot(hosts, items, now=1000)
        self.assertEqual(result["status"], READY)
        self.assertEqual(set(result["devices"]), set(DEFAULT_REQUIRED_HOSTS))

    def test_explicit_unavailable_device_is_not_ready(self):
        hosts, items = live_snapshot(now=1000)
        items[2]["lastvalue"] = "0"
        result = evaluate_network_snapshot(hosts, items, now=1000)
        self.assertEqual(result["status"], NOT_READY)
        self.assertEqual(result["devices"][DEFAULT_REQUIRED_HOSTS[2]]["status"], NOT_READY)

    def test_each_required_device_unavailable_blocks_network_health(self):
        for index, name in enumerate(DEFAULT_REQUIRED_HOSTS):
            with self.subTest(host=name):
                hosts, items = live_snapshot(now=1000)
                items[index]["lastvalue"] = "0"
                result = evaluate_network_snapshot(hosts, items, now=1000)
                self.assertEqual(result["status"], NOT_READY)
                self.assertEqual(result["reason"], f"required SNMP device is unavailable: {name}")

    def test_unknown_interface_availability_fails_closed_without_claiming_device_failure(self):
        hosts, items = live_snapshot(now=1000)
        hosts[0]["interfaces"][0]["available"] = "0"
        result = evaluate_network_snapshot(hosts, items, now=1000)
        self.assertEqual(result["status"], UNKNOWN)
        self.assertIn("indeterminate", result["reason"])

    def test_stale_data_is_unknown_not_a_physical_failure(self):
        hosts, items = live_snapshot(now=1000)
        items[0]["lastclock"] = "600"
        result = evaluate_network_snapshot(hosts, items, now=1000, freshness_seconds=300)
        self.assertEqual(result["status"], UNKNOWN)

    def test_missing_required_host_or_item_is_unknown(self):
        hosts, items = live_snapshot(now=1000)
        result = evaluate_network_snapshot(hosts[:-1], items, now=1000)
        self.assertEqual(result["status"], UNKNOWN)
        result = evaluate_network_snapshot(hosts, items[:-1], now=1000)
        self.assertEqual(result["status"], UNKNOWN)

    def test_benign_warning_and_unsupported_items_are_ignored(self):
        hosts, items = live_snapshot(now=1000)
        items.extend(
            [
                {
                    "host_name": DEFAULT_REQUIRED_HOSTS[0],
                    "name": "Fan2 hardware warning",
                    "lastvalue": "1",
                    "lastclock": "1000",
                    "state": "0",
                    "status": "0",
                },
                {
                    "host_name": DEFAULT_REQUIRED_HOSTS[3],
                    "name": "Unsupported OID",
                    "lastvalue": "",
                    "lastclock": "1000",
                    "state": "1",
                    "status": "0",
                },
            ]
        )
        self.assertEqual(evaluate_network_snapshot(hosts, items, now=1000)["status"], READY)

    def test_provider_reuses_zabbix_api_read_only_snapshot(self):
        now = int(time.time())
        hosts, items = live_snapshot(now=now)

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                import json

                return json.dumps(self.payload).encode("utf-8")

        calls = []

        def opener(request, timeout=8):
            import json

            body = json.loads(request.data.decode("utf-8"))
            calls.append(body["method"])
            if body["method"] == "apiinfo.version":
                return Response({"result": "6.0.0"})
            if body["method"] == "user.login":
                return Response({"result": "session"})
            if body["method"] == "host.get":
                return Response({"result": hosts})
            if body["method"] == "item.get":
                return Response({"result": items})
            raise AssertionError(body["method"])

        provider = ZabbixNetworkHealthProvider(
            api_url="http://zabbix.example",
            api_user="readonly",
            api_password="fixture-only",
            opener=opener,
        )
        result = provider.snapshot()
        self.assertEqual(result["status"], READY)
        self.assertEqual(calls, ["apiinfo.version", "user.login", "host.get", "item.get"])


if __name__ == "__main__":
    unittest.main()
