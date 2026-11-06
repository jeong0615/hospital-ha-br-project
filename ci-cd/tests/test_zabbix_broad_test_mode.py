import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations" / "zabbix"))

from configure_broad_test_mode import (  # noqa: E402
    BroadTestConfigurator,
    DEFAULT_TEST_HOSTS,
    TEST_ACTION_NAME,
    TEST_EXEC_PARAMS,
)
from configure_chatops import Settings  # noqa: E402


def settings():
    return Settings(
        api_url="https://zabbix.example/api_jsonrpc.php",
        api_user="Admin",
        api_password="not-used-in-test",
        api_token="",
        recipient_username="Admin",
        failover_group="Zabbix administrators",
        alert_url="http://172.18.0.1:5001/zabbix/alert",
        failover_sendto="slack",
        failback_sendto="slack",
        alert_sendto="chatops",
    )


def state_with_dependencies(action=None):
    hosts = {
        name: {"hostid": str(index), "name": name, "host": name, "status": "0"}
        for index, name in enumerate(DEFAULT_TEST_HOSTS, start=100)
    }
    media = {
        "Slack DR Test Trigger": {
            "mediatypeid": "401",
            "name": "Slack DR Test Trigger",
            "type": "1",
            "status": "0",
            "exec_path": "send_slack.sh",
            "exec_params": TEST_EXEC_PARAMS,
        },
        "Slack Failback Test": {
            "mediatypeid": "402",
            "name": "Slack Failback Test",
            "type": "1",
            "status": "0",
            "exec_path": "send_failback.sh",
            "exec_params": TEST_EXEC_PARAMS,
        },
    }
    user = {
        "userid": "1",
        "username": "Admin",
        "medias": [
            {
                "mediaid": "501",
                "mediatypeid": "401",
                "sendto": "slack",
                "active": "0",
                "severity": "63",
                "period": "1-7,00:00-24:00",
            },
            {
                "mediaid": "502",
                "mediatypeid": "402",
                "sendto": "slack",
                "active": "0",
                "severity": "63",
                "period": "1-7,00:00-24:00",
            },
        ],
    }
    return {"hosts": hosts, "media": media, "user": user, "action": action}


class BroadTestModeContractTest(unittest.TestCase):
    def test_test_media_uses_existing_scripts_with_event_identity_and_age(self):
        for relative_path, marker in (
            ("operations/chatops/send_slack.sh", "__zabbix_test__"),
            ("operations/chatops/send_failback.sh", "__zabbix_test__"),
        ):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn(marker, source)
            self.assertIn(r'test_mode\":${TEST_MODE}', source)
            self.assertIn("event_age_seconds", source)
            self.assertIn("host", source)
            self.assertIn(r'\"source\":\"zabbix\"', source)
        self.assertNotIn("{HOST.NAME}", TEST_EXEC_PARAMS)
        self.assertNotIn("{EVENT.ID}", TEST_EXEC_PARAMS)
        self.assertNotIn("{EVENT.AGE}", TEST_EXEC_PARAMS)
        configurator_source = (ROOT / "operations/zabbix/configure_broad_test_mode.py").read_text(encoding="utf-8")
        self.assertIn("Event ID: {EVENT.ID}", configurator_source)
        self.assertIn("Event age: {EVENT.AGE}", configurator_source)

    def test_action_is_separate_and_matches_only_named_host_ids(self):
        configurator = BroadTestConfigurator(object(), settings(), DEFAULT_TEST_HOSTS)
        plan = configurator.build_plan(state_with_dependencies(), "0")

        self.assertFalse(plan.errors)
        self.assertEqual(plan.action_create["name"], TEST_ACTION_NAME)
        self.assertEqual(plan.action_create["filter"]["evaltype"], "2")
        self.assertEqual(
            [condition["value"] for condition in plan.action_create["filter"]["conditions"]],
            ["100", "101", "102", "103"],
        )
        self.assertEqual(plan.action_create["operations"][0]["opmessage"]["mediatypeid"], "401")
        self.assertEqual(
            plan.action_create["recovery_operations"][0]["opmessage"]["mediatypeid"], "402"
        )

    def test_existing_compliant_action_is_idempotent_when_enabled(self):
        configurator = BroadTestConfigurator(object(), settings(), DEFAULT_TEST_HOSTS)
        initial = state_with_dependencies()
        create_plan = configurator.build_plan(initial, "0")
        action = dict(create_plan.action_create)
        action.update({"actionid": "900", "status": "0", "filter": create_plan.action_create["filter"]})
        action["operations"][0]["operationid"] = "901"
        action["recovery_operations"][0]["operationid"] = "902"
        state = state_with_dependencies(action)
        plan = configurator.build_plan(state, "0")
        self.assertEqual(plan.changes, [])
        self.assertEqual(plan.errors, [])

    def test_disabled_action_is_not_rewritten_by_read_only_check(self):
        configurator = BroadTestConfigurator(object(), settings(), DEFAULT_TEST_HOSTS)
        initial = state_with_dependencies()
        create_plan = configurator.build_plan(initial, "0")
        action = dict(create_plan.action_create)
        action.update({"actionid": "900", "status": "1", "filter": create_plan.action_create["filter"]})
        action["operations"][0]["operationid"] = "901"
        action["recovery_operations"][0]["operationid"] = "902"
        plan = configurator.build_plan(state_with_dependencies(action), None)
        self.assertEqual(plan.changes, [])
        self.assertEqual(plan.errors, [])


if __name__ == "__main__":
    unittest.main()
