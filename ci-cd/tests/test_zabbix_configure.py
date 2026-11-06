import copy
import json
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ZABBIX_DIR = ROOT / "operations" / "zabbix"
import sys

sys.path.insert(0, str(ZABBIX_DIR))

from configure_chatops import (  # noqa: E402
    ChatOpsConfigurator,
    DR_APPROVAL_MESSAGE,
    DR_APPROVAL_SUBJECT,
    Settings,
    _redact,
    _writable_user_medias,
    _write_json_atomic,
)


def operation(media_id, *, user_id=None, group_id=None, operation_id="1"):
    return {
        "actionid": "dynamic-action",
        "operationid": operation_id,
        "operationtype": "0",
        "esc_step_from": "1",
        "esc_step_to": "1",
        "opconditions": [],
        "opmessage": {"default_msg": "0", "mediatypeid": str(media_id), "message": "m", "subject": "s"},
        "opmessage_grp": ([{"usrgrpid": str(group_id)}] if group_id is not None else []),
        "opmessage_usr": ([{"userid": str(user_id)}] if user_id is not None else []),
    }


class FakeAPI:
    def __init__(self, *, wrong_action3_filter=False, missing_user_media=False):
        self.calls = []
        action3_filter = {"evaltype": "0", "conditions": []} if wrong_action3_filter else {
            "evaltype": "0",
            "eval_formula": "A",
            "formula": "",
            "conditions": [
                {"conditiontype": "26", "operator": "0", "value": "1", "value2": "DR_Escalate"},
                {"conditiontype": "16", "operator": "11"},
            ],
        }
        user_medias = [
            {"mediaid": "501", "userid": "10", "mediatypeid": "140", "sendto": "slack", "active": "0", "severity": "63", "period": "1-7,00:00-24:00"},
            {"mediaid": "502", "userid": "10", "mediatypeid": "143", "sendto": "slack", "active": "0", "severity": "63", "period": "1-7,00:00-24:00"},
        ]
        if not missing_user_media:
            user_medias.append({"mediaid": "503", "userid": "10", "mediatypeid": "144", "sendto": "chatops", "active": "0", "severity": "63", "period": "1-7,00:00-24:00"})
        alert_script = (ROOT / "operations/chatops/zabbix_media_type.js").read_text()
        alert_parameters = [
            {"name": "EventID", "value": "{EVENT.ID}"},
            {"name": "TriggerID", "value": "{TRIGGER.ID}"},
            {"name": "Host", "value": "{HOST.NAME}"},
            {"name": "Severity", "value": "{EVENT.SEVERITY}"},
            {"name": "Status", "value": "{EVENT.STATUS}"},
            {"name": "Value", "value": "{EVENT.VALUE}"},
            {"name": "Subject", "value": "{ALERT.SUBJECT}"},
            {"name": "Message", "value": "{ALERT.MESSAGE}"},
            {"name": "Item", "value": "{ITEM.NAME1}"},
            {"name": "Timestamp", "value": "{EVENT.DATE} {EVENT.TIME}"},
            {"name": "DR_Escalate", "value": "0"},
            {"name": "URL", "value": "http://172.18.0.1:5001/zabbix/alert"},
        ]
        action3_problem = operation("140", group_id="70", operation_id="301")
        action3_recovery = operation("143", user_id="10", operation_id="302")
        for action_operation in (action3_problem, action3_recovery):
            action_operation["opmessage"].update(
                {
                    "default_msg": "0",
                    "subject": DR_APPROVAL_SUBJECT,
                    "message": DR_APPROVAL_MESSAGE,
                }
            )
        self.state = {
            "media": {
                "Slack DR Trigger": {"mediatypeid": "140", "name": "Slack DR Trigger", "type": "1", "status": "0", "exec_path": "send_slack.sh", "exec_params": "__zabbix__\n{ALERT.SUBJECT}\n{ALERT.MESSAGE}\n"},
                "Slack Failback": {"mediatypeid": "143", "name": "Slack Failback", "type": "1", "status": "0", "exec_path": "send_failback.sh", "exec_params": "__zabbix__\n{ALERT.SUBJECT}\n{ALERT.MESSAGE}\n"},
                "ChatOps_Alert": {"mediatypeid": "144", "name": "ChatOps_Alert", "type": "4", "status": "0", "script": alert_script, "parameters": alert_parameters},
            },
            "actions": {
                "Cloud_Slack_DR_Trigger": {"actionid": "30", "name": "Cloud_Slack_DR_Trigger", "filter": action3_filter, "operations": [action3_problem], "recovery_operations": [action3_recovery], "update_operations": []},
                "ChatOps_Alert_Automation": {"actionid": "90", "name": "ChatOps_Alert_Automation", "filter": {"evaltype": "0", "conditions": []}, "operations": [operation("144", user_id="10", operation_id="901")], "recovery_operations": [operation("144", user_id="10", operation_id="902")], "update_operations": []},
            },
            "user": {"userid": "10", "username": "dr-admin", "medias": user_medias},
            "group": {"usrgrpid": "70", "name": "DR operators"},
        }

    def call(self, method, params=None):
        self.calls.append((method, copy.deepcopy(params or {})))
        if method == "mediatype.get":
            name = params["filter"]["name"]
            item = self.state["media"].get(name)
            return [copy.deepcopy(item)] if item else []
        if method == "action.get":
            name = params["filter"]["name"]
            item = self.state["actions"].get(name)
            return [copy.deepcopy(item)] if item else []
        if method == "user.get":
            return [copy.deepcopy(self.state["user"])]
        if method == "usergroup.get":
            return [copy.deepcopy(self.state["group"])]
        raise AssertionError(f"unexpected read call: {method}")


def settings():
    return Settings(
        api_url="https://zabbix.example/api_jsonrpc.php",
        api_user="dr-admin",
        api_password="not-used-in-test",
        api_token="",
        recipient_username="dr-admin",
        failover_group="DR operators",
        alert_url="http://172.18.0.1:5001/zabbix/alert",
        failover_sendto="slack",
        failback_sendto="slack",
        alert_sendto="chatops",
    )


class ZabbixConfiguratorTests(unittest.TestCase):
    def test_correct_named_policy_is_idempotent_and_uses_no_mutating_calls(self):
        api = FakeAPI()
        configurator = ChatOpsConfigurator(api, settings())
        plan = configurator.reconcile()

        self.assertEqual(plan.changes, [])
        self.assertEqual(plan.errors, [])
        self.assertFalse(any(method.endswith(".update") or method.endswith(".create") for method, _ in api.calls))

    def test_action3_filter_drift_is_planned_without_hardcoded_ids(self):
        api = FakeAPI(wrong_action3_filter=True)
        configurator = ChatOpsConfigurator(api, settings())
        plan = configurator.reconcile()

        updates = dict(plan.action_updates)
        self.assertIn("30", updates)
        conditions = updates["30"]["filter"]["conditions"]
        self.assertEqual(conditions[0], {"conditiontype": "26", "operator": "0", "value": "1", "value2": "DR_Escalate"})
        self.assertEqual(conditions[1], {"conditiontype": "16", "operator": "11"})
        self.assertNotIn('"mediatypeid"', json.dumps(updates))

    def test_action3_excludes_suppressed_problems(self):
        api = FakeAPI()
        api.state["actions"]["Cloud_Slack_DR_Trigger"]["filter"]["conditions"] = [
            {"conditiontype": "26", "operator": "0", "value": "1", "value2": "DR_Escalate", "formulaid": "A"}
        ]
        plan = ChatOpsConfigurator(api, settings()).reconcile()
        updates = dict(plan.action_updates)
        self.assertIn("30", updates)
        self.assertIn(
            {"conditiontype": "16", "operator": "11"},
            updates["30"]["filter"]["conditions"],
        )

    def test_action3_message_carries_event_freshness_metadata(self):
        api = FakeAPI()
        for phase in ("operations", "recovery_operations"):
            api.state["actions"]["Cloud_Slack_DR_Trigger"][phase][0]["opmessage"].update(
                {"default_msg": "1", "subject": "", "message": ""}
            )
        plan = ChatOpsConfigurator(api, settings()).reconcile()
        updates = dict(plan.action_updates)
        action3 = updates["30"]
        for phase in ("operations", "recovery_operations"):
            message = action3[phase][0]["opmessage"]
            self.assertEqual(message["default_msg"], "0")
            self.assertIn("Event ID: {EVENT.ID}", message["message"])
            self.assertIn("Event age: {EVENT.AGE}", message["message"])

    def test_missing_user_media_preserves_existing_media_and_adds_only_target(self):
        api = FakeAPI(missing_user_media=True)
        configurator = ChatOpsConfigurator(api, settings())
        plan = configurator.reconcile()

        self.assertIsNotNone(plan.user_media)
        self.assertEqual([item["mediatypeid"] for item in plan.user_media], ["140", "143", "144"])
        self.assertEqual(plan.user_media[0]["sendto"], "slack")

    def test_redaction_covers_secret_key_and_named_secret_parameter(self):
        value = {"passwd": "secret", "parameters": [{"name": "api_token", "value": "secret"}, {"name": "URL", "value": "http://internal"}]}
        redacted = _redact(value)
        self.assertEqual(redacted["passwd"], "<redacted>")
        self.assertEqual(redacted["parameters"][0]["value"], "<redacted>")
        self.assertEqual(redacted["parameters"][1]["value"], "http://internal")

    def test_atomic_export_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "export.json"
            _write_json_atomic(path, {"ok": True})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertEqual(json.loads(path.read_text())["ok"], True)

    def test_user_media_update_strips_read_only_identifiers(self):
        payload = _writable_user_medias(
            [{"mediaid": "501", "userid": "10", "mediatypeid": "140", "sendto": "slack"}]
        )
        self.assertEqual(payload, [{"mediatypeid": "140", "sendto": "slack"}])


if __name__ == "__main__":
    unittest.main()
