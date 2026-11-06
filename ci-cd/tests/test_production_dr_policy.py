import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations" / "zabbix"))

from production_dr_policy import (  # noqa: E402
    DB_HEALTH_ITEM_KEY,
    DOC_FAIL_ITEM_KEY,
    FAILOVER_STABILITY_SECONDS,
    PRODUCTION_TRIGGER_DESCRIPTION,
    ZABBIX_SERVER_HOST,
    WEB_FAIL_ITEM_KEY,
    evaluate_failover_policy,
    failover_expression,
)


class ProductionFailoverPolicyTest(unittest.TestCase):
    def evaluate(self, **overrides):
        values = {
            "web_failed": True,
            "doc_failed": True,
            "db_failed": True,
            "duration_seconds": FAILOVER_STABILITY_SECONDS,
            "monitoring_path_failure": False,
            "maintenance": False,
            "broad_test_mode": False,
            "test_marker": False,
        }
        values.update(overrides)
        return evaluate_failover_policy(**values)

    def test_only_web_failure_escalates_after_sustained_window(self):
        self.assertTrue(self.evaluate(doc_failed=False, db_failed=False))

    def test_only_doc_failure_escalates_after_sustained_window(self):
        self.assertTrue(self.evaluate(web_failed=False, db_failed=False))

    def test_only_db_failure_escalates_after_sustained_window(self):
        self.assertTrue(self.evaluate(web_failed=False, doc_failed=False))

    def test_no_core_service_failure_does_not_escalate(self):
        self.assertFalse(self.evaluate(web_failed=False, doc_failed=False, db_failed=False))

    def test_agent_or_monitoring_path_failure_does_not_escalate(self):
        self.assertFalse(self.evaluate(monitoring_path_failure=True))

    def test_short_core_failure_does_not_escalate(self):
        self.assertFalse(self.evaluate(duration_seconds=FAILOVER_STABILITY_SECONDS - 1))

    def test_sustained_core_failure_escalates(self):
        self.assertTrue(self.evaluate())

    def test_maintenance_blocks_escalation(self):
        self.assertFalse(self.evaluate(maintenance=True))

    def test_broad_test_mode_blocks_production_escalation(self):
        self.assertFalse(self.evaluate(broad_test_mode=True))

    def test_test_marker_blocks_production_escalation(self):
        self.assertFalse(self.evaluate(test_marker=True))

    def test_expression_contains_all_core_items_and_window(self):
        expression = failover_expression()
        self.assertIn(WEB_FAIL_ITEM_KEY, expression)
        self.assertIn(DOC_FAIL_ITEM_KEY, expression)
        self.assertIn(DB_HEALTH_ITEM_KEY, expression)
        self.assertEqual(expression.count(",5m)"), 3)
        self.assertIn(f"max(/{ZABBIX_SERVER_HOST}/{DB_HEALTH_ITEM_KEY},5m)=0", expression)
        self.assertNotIn(f"min(/{ZABBIX_SERVER_HOST}/{DB_HEALTH_ITEM_KEY},5m)=0", expression)
        self.assertIn(" or ", expression)
        self.assertNotIn(" and ", expression)
        self.assertIn(PRODUCTION_TRIGGER_DESCRIPTION.split(":", 1)[0], "Hospital DR")


if __name__ == "__main__":
    unittest.main()
