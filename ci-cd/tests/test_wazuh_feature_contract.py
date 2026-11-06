import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class WazuhFeatureContractTest(unittest.TestCase):
    def test_wazuh_48_features_and_fail_closed_security_contract(self):
        contract = yaml.safe_load(
            (ROOT / "monitoring/wazuh/feature-contract.yml").read_text(encoding="utf-8")
        )
        manager = contract["manager"]
        self.assertTrue(manager["json_alerts"])
        self.assertTrue(manager["indexer_output"])
        for key in ("syscheck", "syscollector", "sca", "vulnerability_detection"):
            self.assertTrue(manager[key]["enabled"])
        self.assertFalse(contract["correlation"]["active_response"])
        self.assertEqual(contract["correlation"]["severity"], "HIGH")
        self.assertTrue(contract["slack"]["deduplicate_by_incident_id"])
        self.assertIn("operations/security/wazuh_chatops_integration.py", (ROOT / "monitoring/wazuh/feature-contract.yml").read_text())


if __name__ == "__main__":
    unittest.main()
