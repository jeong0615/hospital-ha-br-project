import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROXY_DIR = ROOT / "operations" / "chatops"


class ZabbixProxyAclTests(unittest.TestCase):
    def run_acl(self, network="", source="", addresses=()):
        script = (
            "import json, zabbix_host_proxy as p; "
            "print(json.dumps([p.source_is_allowed(item) for item in "
            + repr(tuple(addresses))
            + "]))"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROXY_DIR)
        if network:
            env["ZABBIX_PROXY_ALLOWED_NETWORK"] = network
        else:
            env.pop("ZABBIX_PROXY_ALLOWED_NETWORK", None)
        if source:
            env["ZABBIX_PROXY_ALLOWED_SOURCE"] = source
        else:
            env.pop("ZABBIX_PROXY_ALLOWED_SOURCE", None)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_docker_network_allows_recreated_container_addresses_only(self):
        self.assertEqual(
            self.run_acl(
                network="172.18.0.0/16",
                addresses=("172.18.0.5", "172.18.0.200", "10.0.0.1", "not-an-ip"),
            ),
            [True, True, False, False],
        )

    def test_exact_source_remains_compatible_when_network_is_not_configured(self):
        self.assertEqual(
            self.run_acl(
                source="172.18.0.5",
                addresses=("172.18.0.5", "172.18.0.6"),
            ),
            [True, False],
        )

    def test_network_scope_takes_precedence_over_stale_exact_source(self):
        self.assertEqual(
            self.run_acl(
                network="172.18.0.0/16",
                source="172.18.0.5",
                addresses=("172.18.0.6", "10.0.0.1"),
            ),
            [True, False],
        )

    def test_missing_acl_configuration_fails_closed(self):
        self.assertEqual(self.run_acl(addresses=("172.18.0.5",)), [False])

    def test_unrestricted_network_fails_closed(self):
        self.assertEqual(
            self.run_acl(network="0.0.0.0/0", addresses=("172.18.0.5",)),
            [False],
        )

    def test_systemd_uses_exact_zabbix_bridge_network(self):
        unit = (ROOT / "operations/chatops/systemd/hospital-dr-zabbix-proxy.service").read_text()
        self.assertIn("Environment=ZABBIX_PROXY_ALLOWED_NETWORK=172.18.0.0/16", unit)
        self.assertNotIn("Environment=ZABBIX_PROXY_ALLOWED_SOURCE=", unit)


if __name__ == "__main__":
    unittest.main()
