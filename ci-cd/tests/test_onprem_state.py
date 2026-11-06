import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations" / "monitoring"))
sys.path.insert(0, str(ROOT / "operations" / "preflight"))

from onprem_state import classify_global_path, classify_host  # noqa: E402
from final_live_e2e_preflight import FinalLivePreflight  # noqa: E402


class OnPremStateClassificationTest(unittest.TestCase):
    def test_powered_off_is_not_network_path_blocked(self):
        result = classify_host(power_state="stopped", agent_reachable=False)
        self.assertEqual(result["state"], "POWERED_OFF")
        self.assertNotEqual(result["state"], "NETWORK_PATH_BLOCKED")

    def test_live_agent_can_have_degraded_application_dependency(self):
        result = classify_host(agent_reachable=True, service_healthy=False)
        self.assertEqual(result["state"], "LIVE")
        self.assertEqual(result["service_state"], "SERVICE_UNHEALTHY")

    def test_unavailable_host_without_power_source_is_unknown(self):
        result = classify_host(agent_reachable=False)
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertIn("POWERED_OFF_OR_NOT_AVAILABLE", result["display"])

    def test_global_path_requires_independent_common_failure_signal(self):
        states = {
            "web": {"state": "LIVE"},
            "emr": {"state": "UNKNOWN"},
            "db": {"state": "UNKNOWN"},
        }
        self.assertEqual(classify_global_path(states)["state"], "PASS_PARTIAL_LIVE")
        self.assertEqual(
            classify_global_path(states, common_path_failure=True)["state"],
            "NETWORK_PATH_BLOCKED",
        )

    def test_preflight_does_not_turn_web_live_and_off_peers_into_wg_blocker(self):
        def tcp(host, port, **_kwargs):
            if host == "192.168.200.3" and port == 10050:
                return True, "TCP_CONNECT_PASS"
            return False, "TCP_CONNECT_BLOCKED"

        def http(url, **_kwargs):
            if url.endswith("192.168.200.3/health"):
                return False, "HTTP_500"
            return False, "UNREACHABLE"

        checker = FinalLivePreflight(
            values={"ONPREM_COMMON_PATH_FAILURE": "false"},
            tcp=tcp,
            http=http,
            runner=lambda *_args, **_kwargs: (1, ""),
            include_legacy=False,
        )
        checker.check_onprem_path()
        states = checker.onprem_state
        self.assertEqual(states["web"]["state"], "LIVE")
        self.assertEqual(states["global"]["state"], "PASS_PARTIAL_LIVE")
        self.assertNotIn("ONPREM_COMMON_PATH_FAILURE", checker.blockers)
        self.assertNotIn("ONPREM_WG_IP_INPUT_MISSING", checker.blockers)

    def test_explicit_powered_off_hosts_are_expected_not_wg_failure(self):
        checker = FinalLivePreflight(
            values={
                "ONPREM_WEB_POWER_STATE": "live",
                "ONPREM_DOC_POWER_STATE": "powered_off",
                "ONPREM_DB_POWER_STATE": "stopped",
            },
            tcp=lambda *_args, **_kwargs: (True, "TCP_CONNECT_PASS"),
            http=lambda *_args, **_kwargs: (True, "HTTP_200"),
            runner=lambda *_args, **_kwargs: (1, ""),
            include_legacy=False,
        )
        checker.check_onprem_path()
        self.assertEqual(checker.onprem_state["emr"]["state"], "POWERED_OFF")
        self.assertEqual(checker.onprem_state["db"]["state"], "POWERED_OFF")
        statuses = {item.name: item.status for item in checker.checks}
        self.assertEqual(statuses["onprem_emr_state"], "EXPECTED")
        self.assertEqual(statuses["onprem_db_state"], "EXPECTED")
        self.assertEqual(checker.onprem_state["global"]["state"], "PASS_PARTIAL_LIVE")

    def test_snmp_preflight_can_use_existing_zabbix_host_contract(self):
        names = {
            "Cisco-2800-Series",
            "Catalyst-2950-Series",
            "Catalyst-3560-Series",
            "Catalyst-3550-Series",
        }
        checker = FinalLivePreflight(values={}, include_legacy=False)

        def zabbix_read(_method, params):
            name = params["filter"]["host"]
            return [
                {
                    "hostid": name,
                    "host": name,
                    "interfaces": [{"type": "2", "available": "1", "ip": "172.16.100.254", "port": "161"}],
                }
            ] if name in names else []

        checker._zabbix_api_read = zabbix_read
        checker.check_snmp()
        result = next(item for item in checker.checks if item.name == "snmp_config")
        self.assertEqual(result.status, "PASS")


if __name__ == "__main__":
    unittest.main()
