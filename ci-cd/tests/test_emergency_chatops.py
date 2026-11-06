import hashlib
import hmac
import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from urllib.parse import urlencode


CHATOPS_DIR = Path(__file__).resolve().parents[1] / "operations" / "chatops"
sys.path.insert(0, str(CHATOPS_DIR))

from chatops_core import (  # noqa: E402
    ApprovalStore,
    button_value,
    create_app,
    ensure_emergency_console,
    SlackApiError,
)
from emergency_dr import (  # noqa: E402
    REQUEST_TYPE_DUMP_RESTORE,
    dump_confirm_view,
    dump_select_view,
    emergency_console_blocks,
    emergency_reason_view,
    list_dump_backups,
    reason_options,
    validate_reason,
)


class FakeSlackClient:
    def __init__(self, channel_id):
        self.channel_id = channel_id
        self.posts = []
        self.updates = []
        self.views = []
        self.pins = []
        self.message_checks = []
        self.message_exists_result = True
        self.message_exists_error = None

    @property
    def available(self):
        return True

    def post_message(self, text, blocks):
        self.posts.append({"text": text, "blocks": blocks})
        return {"ok": True, "channel": self.channel_id, "ts": f"4000.{len(self.posts)}"}

    def update_message(self, channel_id, message_ts, text, blocks):
        self.updates.append({"channel": channel_id, "ts": message_ts, "text": text, "blocks": blocks})
        return {"ok": True}

    def message_exists(self, channel_id, message_ts):
        self.message_checks.append((channel_id, message_ts))
        if self.message_exists_error:
            raise SlackApiError("chat.getPermalink", self.message_exists_error)
        return self.message_exists_result

    def open_view(self, trigger_id, view):
        self.views.append({"trigger_id": trigger_id, "view": view})
        return {"ok": True}

    def pin_message(self, channel_id, message_ts):
        self.pins.append((channel_id, message_ts))
        return {"ok": True}


class PinDeniedSlackClient(FakeSlackClient):
    def pin_message(self, channel_id, message_ts):
        raise SlackApiError("pins.add", "missing_scope")


class EmergencyChatOpsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        (root / "full_failover.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (root / "web_db_failback.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (root / "full_failover.sh").chmod(0o700)
        (root / "web_db_failback.sh").chmod(0o700)
        self.dump_dir = root / "db_backups"
        self.dump_dir.mkdir()
        self._write_dump("db_backup_20261002_120000.sql.gz.enc", "2026-10-02T12:00:00Z")
        self._write_dump("db_backup_20261001_120000.sql.gz.enc", "2026-10-01T12:00:00Z")
        invalid_metadata = self.dump_dir / "db_backup_20260929_120000.sql.gz.enc"
        invalid_metadata.write_bytes(b"Salted__invalid-metadata")
        invalid_metadata.with_name("db_backup_20260929_120000.master_status").write_text(
            "BackupTimestamp: 2026-09-29T12:00:00Z\n", encoding="utf-8"
        )
        unencrypted = self.dump_dir / "db_backup_20260928_120000.sql.gz.enc"
        unencrypted.write_bytes(b"not-an-openssl-container")
        unencrypted.with_name("db_backup_20260928_120000.master_status").write_text(
            "BackupTimestamp: 2026-09-28T12:00:00Z\n"
            "BinlogFile: mysql-bin.000001\n"
            "BinlogPosition: 157\n",
            encoding="utf-8",
        )
        (self.dump_dir / "db_backup_20260930_120000.sql.gz.enc").write_bytes(b"incomplete")
        self.failover = FakeSlackClient("C_APPROVAL")
        self.failback = FakeSlackClient("C_APPROVAL")
        self.emergency = FakeSlackClient("C_EMERGENCY")
        state_db = str(root / "state.sqlite3")
        dr_state = root / "dr_state"
        dr_state.write_text("IDLE\n", encoding="utf-8")
        self.app = create_app(
            default_state_db=state_db,
            default_secret_file=str(root / "missing.env"),
            default_script_root=str(root),
            test_config={
                "TESTING": True,
                "CHATOPS_STATE_DB": state_db,
                "DR_STATE_FILE": str(dr_state),
                "DUMP_BACKUP_DIR": str(self.dump_dir),
                "SLACK_EMERGENCY_CHANNEL_ID": "C_EMERGENCY",
                "SLACK_CLIENTS": {
                    "failover": self.failover,
                    "failback": self.failback,
                    "emergency": self.emergency,
                },
                "SLACK_EMERGENCY_CLIENT": self.emergency,
                "ADMIN_USER_IDS": {"U_ADMIN_1", "U_ADMIN_2"},
                "VERIFY_SLACK_SIGNATURE": False,
                "EMERGENCY_PRECHECK_SNAPSHOT": {"status": "PASS", "reason": "fixture"},
                "PIPELINE_LAUNCHER": lambda *args: None,
            },
        )
        self.client = self.app.test_client()
        self.store = self.app.extensions["approval_store"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_dump(self, name, timestamp):
        path = self.dump_dir / name
        path.write_bytes(b"Salted__" + name.encode("utf-8"))
        path.with_name(name[: -len(".sql.gz.enc")] + ".master_status").write_text(
            f"BackupTimestamp: {timestamp}\n"
            "BinlogFile: mysql-bin.000001\n"
            "BinlogPosition: 157\n",
            encoding="utf-8",
        )

    @staticmethod
    def _submit_payload(view, user="U_ADMIN_1"):
        return {"type": "view_submission", "user": {"id": user}, "view": view}

    def test_reason_validation_requires_selection_and_other_detail(self):
        self.assertEqual(validate_reason("failover", "", "")[0], False)
        self.assertEqual(validate_reason("failover", "OTHER", "")[0], False)
        self.assertEqual(validate_reason("failover", "OTHER", "운영자 사유")[0], True)
        self.assertEqual(
            validate_reason("failover", "", "", request_type=REQUEST_TYPE_DUMP_RESTORE)[0],
            False,
        )
        self.assertIn(
            "POINT_IN_TIME_DUMP_REQUIRED",
            {code for code, _ in reason_options("failover", REQUEST_TYPE_DUMP_RESTORE)},
        )

    def test_emergency_dump_entry_requires_admin_and_opens_reason_modal(self):
        payload = {
            "actions": [
                {
                    "value": button_value(
                        "",
                        "failover",
                        "emergency_dump_open",
                        request_type=REQUEST_TYPE_DUMP_RESTORE,
                    )
                }
            ],
            "user": {"id": "U_NOT_ADMIN"},
            "channel": {"id": "C_EMERGENCY"},
            "trigger_id": "TRIGGER-DUMP",
        }
        denied = self.client.post("/slack/action", data={"payload": json.dumps(payload)})
        self.assertIn("등록된 관리자", denied.get_json()["text"])

        payload["user"] = {"id": "U_ADMIN_1"}
        opened = self.client.post("/slack/action", data={"payload": json.dumps(payload)})
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(self.emergency.views[-1]["view"]["callback_id"], "hospital_dr_emergency_reason")
        self.assertIn("긴급 Failover (Dump)", self.emergency.views[-1]["view"]["title"]["text"])

    def test_console_is_idempotent_and_has_no_approval_button(self):
        self.assertEqual(ensure_emergency_console(self.app)["status"], "READY")
        console_store = self.app.extensions["emergency_console_store"]
        state_after_create = console_store.get()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(ensure_emergency_console(self.app)["status"], "READY")
        self.assertEqual(len(self.emergency.posts), 1)
        self.assertEqual(len(self.emergency.updates), 0)
        self.assertEqual(len(self.emergency.message_checks), 1)
        self.assertEqual(console_store.get(), state_after_create)
        self.assertEqual(output.getvalue(), "")
        raw = json.dumps(emergency_console_blocks(button_value), ensure_ascii=False)
        self.assertNotIn("hospital_dr_approve", raw)
        action_ids = [
            element["action_id"]
            for block in emergency_console_blocks(button_value)
            if block.get("type") == "actions"
            for element in block["elements"]
        ]
        self.assertEqual(
            action_ids,
            [
                "hospital_dr_emergency_status",
                "hospital_dr_emergency_failover",
                "hospital_dr_emergency_failback",
                "hospital_dr_emergency_dump",
            ],
        )
        self.assertIn("긴급 Failover (Dump)", raw)
        self.assertNotIn("🚨", raw)
        self.assertNotIn("🔄", raw)
        action_block = next(
            block for block in emergency_console_blocks(button_value) if block.get("type") == "actions"
        )
        styles = {
            element["action_id"]: element.get("style")
            for element in action_block["elements"]
        }
        self.assertIsNone(styles["hospital_dr_emergency_status"])
        self.assertEqual(styles["hospital_dr_emergency_failover"], "danger")
        self.assertEqual(styles["hospital_dr_emergency_failback"], "primary")
        self.assertIsNone(styles["hospital_dr_emergency_dump"])
        self.assertEqual(len(self.emergency.pins), 1)

    def test_console_missing_message_is_recreated_once_and_saved(self):
        self.assertEqual(ensure_emergency_console(self.app)["status"], "READY")
        console_store = self.app.extensions["emergency_console_store"]
        old_message_ts = console_store.get()["message_ts"]
        self.emergency.message_exists_result = False
        result = ensure_emergency_console(self.app)
        self.assertEqual(result["status"], "READY")
        new_message_ts = console_store.get()["message_ts"]
        self.assertNotEqual(new_message_ts, old_message_ts)
        self.assertEqual(len(self.emergency.posts), 2)
        self.assertEqual(len(self.emergency.updates), 0)
        self.assertEqual(len(self.emergency.pins), 2)

        self.emergency.message_exists_error = None
        self.emergency.message_exists_result = True
        self.assertEqual(ensure_emergency_console(self.app)["status"], "READY")
        self.assertEqual(len(self.emergency.posts), 2)
        self.assertEqual(len(self.emergency.pins), 2)

    def test_console_presence_api_failure_is_graceful_and_retried_later(self):
        self.assertEqual(ensure_emergency_console(self.app)["status"], "READY")
        console_store = self.app.extensions["emergency_console_store"]
        state_before = console_store.get()
        self.emergency.message_exists_error = "ratelimited"

        result = ensure_emergency_console(self.app)

        self.assertEqual(result, {"status": "ERROR", "error": "ratelimited"})
        self.assertEqual(console_store.get(), state_before)
        self.assertEqual(len(self.emergency.posts), 1)
        self.assertEqual(len(self.emergency.updates), 0)
        self.assertEqual(len(self.emergency.pins), 1)

    def test_console_watchdog_uses_sixty_seconds_and_cleanup_stays_at_three_hundred(self):
        self.assertEqual(self.app.config["EMERGENCY_CONSOLE_WATCHDOG_INTERVAL_SECONDS"], 60)
        self.assertEqual(self.app.config["CHATOPS_SLACK_CLEANUP_INTERVAL_SECONDS"], 300)

    def test_console_pin_missing_scope_is_graceful(self):
        self.app.extensions["slack_clients"]["emergency"] = PinDeniedSlackClient("C_EMERGENCY")
        result = ensure_emergency_console(self.app)
        self.assertEqual(result["status"], "READY")

    def test_emergency_client_does_not_fallback_to_failover_token(self):
        root = Path(self.temp_dir.name)
        secret_file = root / "synthetic-dr.env"
        secret_file.write_text(
            "SLACK_CHANNEL_ID=C_APPROVAL\n"
            "SLACK_EMERGENCY_CHANNEL_ID=C_EMERGENCY\n"
            "SLACK_FAILOVER_BOT_TOKEN=synthetic-failover-token\n",
            encoding="utf-8",
        )
        state_db = str(root / "separate-client.sqlite3")
        app = create_app(
            default_state_db=state_db,
            default_secret_file=str(secret_file),
            default_script_root=str(root),
            test_config={"TESTING": True, "CHATOPS_STATE_DB": state_db},
        )
        clients = app.extensions["slack_clients"]
        self.assertEqual(clients["failover"].token, "synthetic-failover-token")
        self.assertEqual(clients["emergency"].token, "")

    def test_emergency_signature_is_separate_and_required(self):
        root = Path(self.temp_dir.name)
        state_db = str(root / "signature.sqlite3")
        emergency = FakeSlackClient("C_EMERGENCY")
        app = create_app(
            default_state_db=state_db,
            default_secret_file=str(root / "missing.env"),
            default_script_root=str(root),
            test_config={
                "TESTING": True,
                "CHATOPS_STATE_DB": state_db,
                "SLACK_EMERGENCY_CHANNEL_ID": "C_EMERGENCY",
                "SLACK_CLIENTS": {
                    "failover": self.failover,
                    "failback": self.failback,
                    "emergency": emergency,
                },
                "SLACK_EMERGENCY_CLIENT": emergency,
                "SLACK_SIGNING_SECRETS": {
                    "failover": "synthetic-failover-signing",
                    "failback": "synthetic-failback-signing",
                    "emergency": "synthetic-emergency-signing",
                },
                "ADMIN_USER_IDS": {"U_ADMIN_1", "U_ADMIN_2"},
                "VERIFY_SLACK_SIGNATURE": True,
            },
        )
        client = app.test_client()
        payload = {
            "actions": [
                {
                    "value": button_value(
                        "", "failover", "emergency_status"
                    )
                }
            ],
            "user": {"id": "U_ADMIN_1"},
            "channel": {"id": "C_EMERGENCY"},
            "trigger_id": "TRIGGER-STATUS",
        }
        body = urlencode({"payload": json.dumps(payload, separators=(",", ":"))}).encode()

        def signed(secret):
            timestamp = str(int(time.time()))
            base = b"v0:" + timestamp.encode("ascii") + b":" + body
            signature = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
            return timestamp, signature

        timestamp, wrong_signature = signed("synthetic-failover-signing")
        wrong = client.post(
            "/slack/action",
            data=body,
            content_type="application/x-www-form-urlencoded",
            headers={
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": wrong_signature,
            },
        )
        self.assertEqual(wrong.status_code, 401)

        timestamp, correct_signature = signed("synthetic-emergency-signing")
        correct = client.post(
            "/slack/action",
            data=body,
            content_type="application/x-www-form-urlencoded",
            headers={
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": correct_signature,
            },
        )
        self.assertEqual(correct.status_code, 200)

    def test_non_admin_and_reason_errors_are_rejected(self):
        view = emergency_reason_view("failover", "EMERGENCY_FAILOVER")
        response = self.client.post(
            "/slack/failover/action",
            data={"payload": json.dumps(self._submit_payload(view, "U_NOT_ADMIN"))},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("등록된 관리자", json.dumps(response.get_json(), ensure_ascii=False))

        values = {
            "emergency_reason_code": {"reason_code": {"selected_option": {"value": "OTHER"}}},
            "emergency_reason_detail": {"reason_detail": {"value": ""}},
        }
        view["state"] = {"values": values}
        response = self.client.post(
            "/slack/failover/action",
            data={"payload": json.dumps(self._submit_payload(view))},
        )
        self.assertIn("기타 사유", json.dumps(response.get_json(), ensure_ascii=False))

    def test_emergency_request_has_15_minute_ttl_and_two_approvals(self):
        view = emergency_reason_view("failover", "EMERGENCY_FAILOVER")
        view["state"] = {
            "values": {
                "emergency_reason_code": {
                    "reason_code": {"selected_option": {"value": "IMMEDIATE_RECOVERY_REQUIRED"}}
                },
                "emergency_reason_detail": {"reason_detail": {"value": ""}},
            }
        }
        response = self.client.post(
            "/slack/failover/action",
            data={"payload": json.dumps(self._submit_payload(view))},
        )
        self.assertEqual(response.get_json()["response_action"], "clear")
        state = self.store.find_active("failover")
        self.assertEqual(state["incidents"][-1]["request_type"], "EMERGENCY_FAILOVER")
        self.assertLessEqual(state["expires_at"] - state["created_at"], 900.5)

        for user in ("U_ADMIN_1", "U_ADMIN_2"):
            payload = {
                "actions": [{"value": button_value(state["request_id"], "failover", "approve")}],
                "user": {"id": user},
                "channel": {"id": "C_EMERGENCY"},
                "container": {"message_ts": "4000.1", "channel_id": "C_EMERGENCY"},
            }
            self.client.post("/slack/action", data={"payload": json.dumps(payload)})
            if user == "U_ADMIN_1":
                partial = self.store.get(state["request_id"])
                self.assertLessEqual(partial["expires_at"] - partial["created_at"], 900.5)
        self.assertEqual(self.store.get(state["request_id"])["status"], "EXECUTING")

    def test_dump_inventory_is_newest_and_complete_only(self):
        backups = list_dump_backups(self.dump_dir)
        self.assertEqual(len(backups), 2)
        self.assertGreater(backups[0]["timestamp"], backups[1]["timestamp"])
        self.assertTrue(backups[0]["checksum"])
        self.assertEqual(backups[0]["master_status_file"], "mysql-bin.000001")
        self.assertEqual(backups[0]["master_status_position"], 157)
        self.assertEqual(backups[0]["encryption_status"], "CONTAINER_MARKER_PASS")
        self.assertEqual(backups[0]["integrity_status"], "PAIR_METADATA_CONTAINER_PASS")
        view = dump_select_view("failover", self.dump_dir, "emergency")
        options = view["blocks"][-1]["element"]["options"]
        self.assertEqual(len(options), 2)
        confirmation = dump_confirm_view("failover", backups[0])
        confirmation_text = confirmation["blocks"][0]["text"]["text"]
        self.assertIn("mysql-bin.000001:157", confirmation_text)
        self.assertIn("CONTAINER_MARKER_PASS", confirmation_text)
        self.assertIn("PAIR_METADATA_CONTAINER_PASS", confirmation_text)

    def test_dump_confirmation_creates_request_but_disabled_executor_cannot_run(self):
        view = dump_select_view("failover", self.dump_dir, "failover")
        view["state"] = {
            "values": {
                "dump_backup": {"backup_id": {"selected_option": {"value": "db_backup_20261002_120000.sql.gz.enc"}}}
            }
        }
        updated = self.client.post(
            "/slack/failover/action",
            data={"payload": json.dumps(self._submit_payload(view))},
        ).get_json()
        self.assertEqual(updated["response_action"], "update")
        confirm = updated["view"]
        confirm["state"] = {
            "values": {
                "dump_confirm": {"confirm_choice": {"selected_option": {"value": "YES"}}}
            }
        }
        response = self.client.post(
            "/slack/failover/action",
            data={"payload": json.dumps(self._submit_payload(confirm))},
        )
        self.assertEqual(response.get_json()["response_action"], "clear")
        state = self.store.find_active("failover")
        self.assertEqual(state["incidents"][-1]["request_type"], REQUEST_TYPE_DUMP_RESTORE)
        self.assertEqual(state["incidents"][-1]["request_origin"], "GENERAL")

        approval_payload = {
            "actions": [{"value": button_value(state["request_id"], "failover", "approve")}],
            "user": {"id": "U_ADMIN_1"},
            "channel": {"id": "C_APPROVAL"},
            "container": {"message_ts": "4100.1", "channel_id": "C_APPROVAL"},
        }
        blocked = self.client.post(
            "/slack/action", data={"payload": json.dumps(approval_payload)}
        )
        self.assertIn("DUMP_RESTORE_EXEC_ENABLED", blocked.get_json()["text"])
        self.assertNotEqual(self.store.get(state["request_id"])["status"], "EXECUTING")

    def test_emergency_dump_flow_preserves_reason_origin_and_ttl(self):
        button_payload = {
            "actions": [
                {
                    "value": button_value(
                        "",
                        "failover",
                        "emergency_dump_open",
                        request_type=REQUEST_TYPE_DUMP_RESTORE,
                    )
                }
            ],
            "user": {"id": "U_ADMIN_1"},
            "channel": {"id": "C_EMERGENCY"},
            "trigger_id": "TRIGGER-EMERGENCY-DUMP",
        }
        self.client.post("/slack/action", data={"payload": json.dumps(button_payload)})
        reason_view = self.emergency.views[-1]["view"]
        reason_view["state"] = {
            "values": {
                "emergency_reason_code": {
                    "reason_code": {"selected_option": {"value": "POINT_IN_TIME_DUMP_REQUIRED"}}
                },
                "emergency_reason_detail": {"reason_detail": {"value": "특정 시점 복구"}},
            }
        }
        pushed = self.client.post(
            "/slack/failover/action",
            data={"payload": json.dumps(self._submit_payload(reason_view))},
        ).get_json()
        self.assertEqual(pushed["response_action"], "push")
        select_view = pushed["view"]
        select_view["state"] = {
            "values": {
                "dump_backup": {
                    "backup_id": {
                        "selected_option": {"value": "db_backup_20261002_120000.sql.gz.enc"}
                    }
                }
            }
        }
        confirmed = self.client.post(
            "/slack/failover/action",
            data={"payload": json.dumps(self._submit_payload(select_view))},
        ).get_json()
        self.assertEqual(confirmed["response_action"], "update")
        confirm_view = confirmed["view"]
        confirm_view["state"] = {
            "values": {
                "dump_confirm": {
                    "confirm_choice": {"selected_option": {"value": "YES"}}
                }
            }
        }
        created = self.client.post(
            "/slack/failover/action",
            data={"payload": json.dumps(self._submit_payload(confirm_view))},
        )
        self.assertEqual(created.get_json()["response_action"], "clear")
        state = self.store.find_active("failover")
        incident = state["incidents"][-1]
        self.assertEqual(incident["request_type"], REQUEST_TYPE_DUMP_RESTORE)
        self.assertEqual(incident["request_origin"], "EMERGENCY")
        self.assertEqual(incident["reason_code"], "POINT_IN_TIME_DUMP_REQUIRED")
        self.assertLessEqual(state["expires_at"] - state["created_at"], 900.5)
        self.assertEqual(state["slack_client_key"], "emergency")

    def test_dump_checksum_change_is_rejected_before_request_creation(self):
        view = dump_select_view("failover", self.dump_dir, "failover")
        view["state"] = {
            "values": {
                "dump_backup": {
                    "backup_id": {
                        "selected_option": {"value": "db_backup_20261002_120000.sql.gz.enc"}
                    }
                }
            }
        }
        updated = self.client.post(
            "/slack/failover/action",
            data={"payload": json.dumps(self._submit_payload(view))},
        ).get_json()
        confirm = updated["view"]
        (self.dump_dir / "db_backup_20261002_120000.sql.gz.enc").write_bytes(b"Salted__changed-after-selection")
        confirm["state"] = {
            "values": {
                "dump_confirm": {
                    "confirm_choice": {"selected_option": {"value": "YES"}}
                }
            }
        }
        response = self.client.post(
            "/slack/failover/action",
            data={"payload": json.dumps(self._submit_payload(confirm))},
        )
        self.assertEqual(response.get_json()["response_action"], "errors")
        self.assertIn("checksum", json.dumps(response.get_json(), ensure_ascii=False).lower())

    def test_dump_confirmation_no_cancels_without_request(self):
        backup = list_dump_backups(self.dump_dir)[0]
        confirm = dump_confirm_view("failover", backup, {"slack_client_key": "failover"})
        confirm["state"] = {
            "values": {
                "dump_confirm": {
                    "confirm_choice": {"selected_option": {"value": "NO"}}
                }
            }
        }
        response = self.client.post(
            "/slack/failover/action",
            data={"payload": json.dumps(self._submit_payload(confirm))},
        )
        self.assertEqual(response.get_json()["response_action"], "clear")
        self.assertIsNone(self.store.find_active("failover"))

    def test_expiry_discards_emergency_request(self):
        incident = {
            "incident_id": "emg-expire",
            "request_type": "EMERGENCY_FAILOVER",
            "slack_client_key": "emergency",
            "source": "emergency_console",
        }
        state, _ = self.store.create_or_merge("failover", incident, now=100.0, ttl_seconds=900)
        expired = self.store.expire_due(now=1001.0)
        self.assertEqual(expired[0]["request_id"], state["request_id"])
        self.assertEqual(self.store.get(state["request_id"])["status"], "EXPIRED")

    def test_global_execution_lock_rejects_second_approval(self):
        now = time.time()
        first, _ = self.store.create_or_merge("failover", {"incident_id": "one"}, now=now)
        second, _ = self.store.create_or_merge("failback", {"incident_id": "two"}, now=now)
        self.assertEqual(self.store.approve(first["request_id"], "failover", "U_ADMIN_1")[0], "pending")
        self.assertEqual(self.store.approve(first["request_id"], "failover", "U_ADMIN_2")[0], "approved")
        result, active = self.store.approve(second["request_id"], "failback", "U_ADMIN_1")
        self.assertEqual(result, "global_lock")
        self.assertEqual(active["request_id"], first["request_id"])


if __name__ == "__main__":
    unittest.main()
