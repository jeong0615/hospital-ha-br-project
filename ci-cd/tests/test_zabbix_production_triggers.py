import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations" / "zabbix"))

from configure_production_dr_triggers import (  # noqa: E402
    AGENT_UNAVAILABLE_DESCRIPTION,
    DB_ITEM_NAME,
    Plan,
    ProductionTriggerConfigurator,
)
from production_dr_policy import (  # noqa: E402
    DB_HEALTH_ITEM_KEY,
    DOC_FAIL_ITEM_KEY,
    DOC_SCENARIO,
    PRODUCTION_TRIGGER_DESCRIPTION,
    WEB_FAIL_ITEM_KEY,
    WEB_SCENARIO,
    production_trigger_tags,
)


def compliant_state():
    hosts = {
        "web_server": {"hostid": "10645", "name": "web_server"},
        "DB_Server": {"hostid": "10643", "name": "DB_Server"},
        "Doc_Server": {"hostid": "10644", "name": "Doc_Server"},
        "Zabbix server": {"hostid": "10084", "name": "Zabbix server"},
    }
    def scenario(spec):
        return {
            "httptestid": "5" if spec is WEB_SCENARIO else "6",
            "hostid": "10084",
            **copy.deepcopy(spec),
        }
    def agent(hostid, triggerid):
        return {
            "hostid": hostid,
            "triggerid": triggerid,
            "description": AGENT_UNAVAILABLE_DESCRIPTION,
            "tags": [{"tag": "scope", "value": "availability"}],
        }
    agent_triggers = {
        "web_server": agent("10645", "24361"),
        "DB_Server": agent("10643", "24315"),
        "Doc_Server": agent("10644", "24338"),
    }
    aggregate = {
        "triggerid": "30000",
        "hostid": "10084",
        "description": PRODUCTION_TRIGGER_DESCRIPTION,
        "expression": "({7001}=1 or {7002}=1 or {7003}=0)",
        "recovery_expression": "",
        "priority": "4",
        "status": "0",
        "tags": production_trigger_tags(),
        "functions": [
            {"itemid": "7001", "function": "min", "parameter": "$,5m"},
            {"itemid": "7002", "function": "min", "parameter": "$,5m"},
            {"itemid": "7003", "function": "max", "parameter": "$,5m"},
        ],
        "dependencies": [
            {"triggerid": "24361"},
            {"triggerid": "24315"},
            {"triggerid": "24338"},
        ],
    }
    items = [
        {"itemid": "7001", "key_": WEB_FAIL_ITEM_KEY, "type": "0", "value_type": "3", "delay": "1m", "status": "0"},
        {"itemid": "7002", "key_": DOC_FAIL_ITEM_KEY, "type": "0", "value_type": "3", "delay": "1m", "status": "0"},
        {"itemid": "7003", "key_": DB_HEALTH_ITEM_KEY, "type": "3", "value_type": "3", "delay": "60s", "status": "0", "name": DB_ITEM_NAME},
    ]
    return {
        "hosts": hosts,
        "scenarios": {WEB_SCENARIO["name"]: scenario(WEB_SCENARIO), DOC_SCENARIO["name"]: scenario(DOC_SCENARIO)},
        "items": items,
        "aggregate": aggregate,
        "agent_triggers": agent_triggers,
        "all_escalations": [aggregate],
    }


class ProductionTriggerPlanTest(unittest.TestCase):
    def test_compliant_runtime_is_idempotent(self):
        plan = ProductionTriggerConfigurator(object()).build_plan(compliant_state())
        self.assertIsInstance(plan, Plan)
        self.assertEqual(plan.changes, [])
        self.assertEqual(plan.errors, [])

    def test_retired_all_core_and_expression_is_detected_as_drift(self):
        state = compliant_state()
        state["aggregate"]["expression"] = "{7001}=1 and {7002}=1 and {7003}=0"
        plan = ProductionTriggerConfigurator(object()).build_plan(state)
        self.assertIn(
            f"update production trigger {PRODUCTION_TRIGGER_DESCRIPTION}",
            plan.changes,
        )
        self.assertFalse(plan.errors)

    def test_missing_dependency_is_planned_without_deleting_other_state(self):
        state = compliant_state()
        state["aggregate"]["dependencies"] = [{"triggerid": "24361"}]
        plan = ProductionTriggerConfigurator(object()).build_plan(state)
        self.assertTrue(any("dependency" in change for change in plan.changes))
        self.assertFalse(plan.errors)

    def test_retired_test_escalation_requires_manual_cleanup(self):
        state = compliant_state()
        state["all_escalations"].append(
            {
                "triggerid": "24624",
                "hostid": "10662",
                "description": "DR TEST: retired test trigger",
                "tags": [{"tag": "DR_Escalate", "value": "1"}],
            }
        )
        plan = ProductionTriggerConfigurator(object()).build_plan(state)
        self.assertTrue(any("retired/test object" in error for error in plan.errors))

    def test_unknown_escalation_trigger_blocks_automatic_reconciliation(self):
        state = compliant_state()
        state["all_escalations"].append({"triggerid": "999", "hostid": "10084", "description": "unrelated", "tags": [{"tag": "DR_Escalate", "value": "1"}]})
        plan = ProductionTriggerConfigurator(object()).build_plan(state)
        self.assertTrue(plan.errors)
        self.assertIn("unexpected DR_Escalate", plan.errors[0])


if __name__ == "__main__":
    unittest.main()
