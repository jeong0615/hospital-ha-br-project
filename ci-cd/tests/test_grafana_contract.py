import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "monitoring/grafana/provisioning/dashboards/json/hospital-infrastructure.json"


class GrafanaContractTest(unittest.TestCase):
    def test_infrastructure_dashboard_is_valid_and_reproducible(self):
        document = json.loads(DASHBOARD.read_text(encoding="utf-8"))
        self.assertEqual(document["uid"], "hospital-dr-infrastructure")
        self.assertEqual(document["title"], "Hospital DR Infrastructure")
        titles = {panel["title"] for panel in document["panels"]}
        self.assertTrue(
            {
                "Zabbix Server Availability",
                "Zabbix Server Agent Ping",
                "On-Prem Agent Path",
                "On-Prem Web Agent / Runtime",
                "Router / Catalyst Reachability",
                "Router / Catalyst SNMP Availability",
                "On-Prem CPU",
                "On-Prem Memory Available",
                "On-Prem Disk Used",
            }.issubset(titles)
        )

        serialized = DASHBOARD.read_text(encoding="utf-8")
        for forbidden in ("SLACK_", "PASSWORD", "TOKEN", "PRIVATE_KEY", "SECRET"):
            self.assertNotIn(forbidden, serialized)

        panels = {panel["title"]: panel for panel in document["panels"]}
        availability = panels["Zabbix Server Availability"]["targets"][0]
        self.assertEqual(availability["group"]["filter"], "Zabbix servers")
        self.assertEqual(availability["item"]["filter"], "Linux: Zabbix agent availability")
        ping = panels["Zabbix Server Agent Ping"]["targets"][0]
        self.assertEqual(ping["group"]["filter"], "Zabbix servers")
        self.assertEqual(ping["item"]["filter"], "Linux: Zabbix agent ping")
        for target in panels["Router / Catalyst Reachability"]["targets"]:
            self.assertEqual(target["group"]["filter"], "Templates/Network devices")
            self.assertEqual(target["item"]["filter"], "Cisco IOS: ICMP ping")
        for target in panels["Router / Catalyst SNMP Availability"]["targets"]:
            self.assertEqual(target["group"]["filter"], "Templates/Network devices")
            self.assertEqual(target["item"]["filter"], "Cisco IOS: SNMP agent availability")
        web = panels["On-Prem Web Agent / Runtime"]["targets"][0]
        self.assertEqual(web["host"]["filter"], "web_server")
        self.assertEqual(web["item"]["filter"], "Linux: Zabbix agent availability")
        self.assertIn("PASS_PARTIAL_LIVE", panels["On-Prem Agent Path"]["options"]["content"])
        self.assertIn("POWERED OFF / NOT TESTED LIVE", panels["On-Prem Agent Path"]["options"]["content"])
        self.assertIn("DEGRADED / NOT CONFIRMED", panels["On-Prem Agent Path"]["options"]["content"])
        for title in ("On-Prem CPU", "On-Prem Memory Available", "On-Prem Disk Used"):
            self.assertEqual(panels[title]["type"], "text")
            self.assertIn("POWERED OFF / NOT TESTED LIVE", panels[title]["options"]["content"])

    def test_wazuh_dashboard_uses_real_timestamp_and_security_panels(self):
        dashboard = ROOT / "monitoring/grafana/provisioning/dashboards/json/wazuh-security-summary.json"
        document = json.loads(dashboard.read_text(encoding="utf-8"))
        self.assertEqual(document["uid"], "wazuh-security-summary")
        titles = {panel["title"] for panel in document["panels"]}
        self.assertTrue({"Authentication Events", "FIM Events", "SCA Events", "Vulnerability Events"}.issubset(titles))
        serialized = dashboard.read_text(encoding="utf-8")
        self.assertIn('"timeField": "timestamp"', serialized)
        self.assertNotIn('"timeField": "@timestamp"', serialized)

    def test_security_operations_dashboard_has_real_sources_and_explicit_blocked_states(self):
        dashboard = ROOT / "monitoring/grafana/provisioning/dashboards/json/hospital-dr-security-operations.json"
        document = json.loads(dashboard.read_text(encoding="utf-8"))
        self.assertEqual(document["uid"], "hospital-dr-security-operations")
        serialized = dashboard.read_text(encoding="utf-8")
        self.assertIn("hospital-devsecops-executions", serialized)
        self.assertIn("RPO", serialized)
        self.assertIn("NR NOT READY", serialized)
        self.assertIn('"timeField": "timestamp"', serialized)

        panels = {panel["title"]: panel for panel in document["panels"]}
        for title in (
            "Wazuh Alerts",
            "Wazuh High / Critical",
            "DevSecOps Runs",
            "Trivy HIGH Findings",
            "Trivy CRITICAL Findings",
            "Correlated Security Incidents",
        ):
            panel = panels[title]
            target = panel["targets"][0]
            self.assertTrue(target["bucketAggs"])
            self.assertEqual(target["bucketAggs"][0]["type"], "date_histogram")
            self.assertEqual(target["bucketAggs"][0]["settings"]["interval"], "1h")
            self.assertEqual(panel["options"]["reduceOptions"]["calcs"], ["sum"])
        self.assertEqual(panels["Zabbix Server Availability"]["targets"][0]["group"]["filter"], "Zabbix servers")
        self.assertEqual(len(panels["Wazuh Alerts by Agent"]["targets"]), 4)

    def test_wazuh_and_devsecops_panels_have_renderable_real_queries(self):
        dashboard = ROOT / "monitoring/grafana/provisioning/dashboards/json/wazuh-security-summary.json"
        document = json.loads(dashboard.read_text(encoding="utf-8"))
        panels = {panel["title"]: panel for panel in document["panels"]}
        for title in (
            "Total Alerts",
            "Authentication Events",
            "FIM Events",
            "SCA Events",
            "Vulnerability Events",
        ):
            target = panels[title]["targets"][0]
            self.assertEqual(target["timeField"], "timestamp" if title != "Vulnerability Events" else "vulnerability.detected_at")
            self.assertEqual(target["bucketAggs"][0]["type"], "date_histogram")
            self.assertEqual(target["bucketAggs"][0]["settings"]["interval"], "1h")
        self.assertEqual(len(panels["Alerts by Agent"]["targets"]), 4)
        self.assertEqual(len(panels["Top Rules"]["targets"]), 5)
        self.assertNotIn('"interval": "auto"', dashboard.read_text(encoding="utf-8"))

    def test_dr_dashboard_uses_explicit_empty_state_instead_of_no_data(self):
        automation = json.loads((ROOT / "monitoring/grafana/provisioning/dashboards/json/hospital-dr-automation.json").read_text(encoding="utf-8"))
        panels = {panel["title"]: panel for panel in automation["panels"]}
        self.assertEqual(panels["Timeline Timestamps"]["type"], "text")
        self.assertIn("NO COMPLETED DR RUN", panels["Timeline Timestamps"]["options"]["content"])
        self.assertIn("NR NOT READY", panels["NR IO Status / RPO: NR NOT READY"]["fieldConfig"]["defaults"]["noValue"])
        self.assertIn("NR BLOCKED", panels["Replication Lag"]["fieldConfig"]["defaults"]["noValue"])

        rto = json.loads((ROOT / "monitoring/grafana/provisioning/dashboards/json/hospital-dr-rto.json").read_text(encoding="utf-8"))
        rto_panels = {panel["title"]: panel for panel in rto["panels"]}
        self.assertEqual(rto_panels["RTO Target"]["type"], "text")
        self.assertIn("1800 seconds / 30 minutes", rto_panels["RTO Target"]["options"]["content"])
        self.assertIn("NO COMPLETED DR RUN", rto_panels["Current / Latest RTO"]["fieldConfig"]["defaults"]["noValue"])
        for title in (
            "RTO PASS / FAIL",
            "Incident → Detection",
            "Detection → Approval Request",
            "Approval Request → Approved",
            "Approved → Failover Start",
            "Failover Start → Service Recovery",
        ):
            self.assertEqual(rto_panels[title]["type"], "text")
            self.assertIn("NOT MEASURED", rto_panels[title]["options"]["content"])

    def test_wazuh_vulnerability_panel_uses_indexed_state_documents(self):
        dashboard = ROOT / "monitoring/grafana/provisioning/dashboards/json/wazuh-security-summary.json"
        document = json.loads(dashboard.read_text(encoding="utf-8"))
        vulnerability = next(panel for panel in document["panels"] if panel["title"] == "Vulnerability Events")
        self.assertEqual(vulnerability["datasource"]["uid"], "PHOSPITALWAZUHVULN")
        target = vulnerability["targets"][0]
        self.assertEqual(target["timeField"], "vulnerability.detected_at")
        self.assertEqual(target["query"], "*")
        self.assertIn("NO INDEXED FINDINGS", json.dumps(vulnerability))

    def test_custom_indexer_datasources_are_reproducible(self):
        source = (ROOT / "monitoring/grafana/provisioning/datasources/custom-indexers.yml").read_text()
        self.assertIn("Hospital-DevSecOps-Indexer", source)
        self.assertIn("Hospital-Security-Incidents-Indexer", source)
        self.assertIn("Hospital-Wazuh-Vulnerability-State", source)
        self.assertIn("wazuh-states-vulnerabilities-*", source)

    def test_grafana_indexer_role_is_read_only_and_scoped(self):
        document = json.loads((ROOT / "monitoring/wazuh/grafana-readonly-role.json").read_text())
        self.assertEqual(document["role"], "grafana_wazuh_readonly")
        self.assertEqual(document["cluster_permissions"], ["cluster_monitor"])
        permission = document["index_permissions"][0]
        self.assertEqual(permission["allowed_actions"], ["read"])
        self.assertNotIn("*", permission["index_patterns"])
        self.assertIn("hospital-devsecops-executions", permission["index_patterns"])
        self.assertIn("hospital-security-incidents", permission["index_patterns"])


if __name__ == "__main__":
    unittest.main()
