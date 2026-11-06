import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations" / "preflight"))

from final_live_e2e_preflight import (  # noqa: E402
    Check,
    FinalLivePreflight,
    assess_readiness,
    configured_indexer_url,
    parse_env_file,
    render_summary,
    write_json,
)
from rpo_evidence import collect  # noqa: E402


class FinalLiveE2EPreflightTest(unittest.TestCase):
    def test_indexer_runtime_url_is_accepted_when_controller_override_is_absent(self):
        self.assertEqual(
            configured_indexer_url({"WAZUH_INDEXER_URL": "https://indexer.example"}),
            "https://indexer.example",
        )
        self.assertEqual(
            configured_indexer_url(
                {
                    "WAZUH_CONTROLLER_INDEXER_URL": "https://controller.example",
                    "WAZUH_INDEXER_URL": "https://indexer.example",
                }
            ),
            "https://controller.example",
        )

    def test_zabbix_failback_runtime_requires_matching_executable_scripts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "operations/chatops").mkdir(parents=True)
            (root / "operations/db").mkdir(parents=True)
            runtime = root / "zabbix-alertscripts"
            runtime.mkdir()
            for relative in ("operations/chatops/send_failback.sh", "operations/db/zabbix_failback_guard.sh"):
                source = root / relative
                source.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
                installed = runtime / source.name
                installed.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
                os.chmod(installed, 0o750)
            checker = FinalLivePreflight(
                root=root,
                values={"ZABBIX_ALERTSCRIPTS_DIR": str(runtime)},
                runner=lambda *_args, **_kwargs: (0, ""),
                include_legacy=False,
            )
            checker.check_zabbix_failback_runtime()
            self.assertEqual(checker.checks[-1].status, "PASS")
            (runtime / "send_failback.sh").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
            checker = FinalLivePreflight(
                root=root,
                values={"ZABBIX_ALERTSCRIPTS_DIR": str(runtime)},
                runner=lambda *_args, **_kwargs: (0, ""),
                include_legacy=False,
            )
            checker.check_zabbix_failback_runtime()
            self.assertEqual(checker.checks[-1].status, "BLOCKED")

    def test_zabbix_failback_runtime_accepts_active_compose_bind_mount_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "operations/chatops").mkdir(parents=True)
            (root / "operations/db").mkdir(parents=True)
            wrapper = root / "send_failback.sh"
            wrapper.write_text(
                f'#!/usr/bin/env bash\nexec bash {root}/operations/chatops/send_failback.sh "$@"\n',
                encoding="utf-8",
            )
            os.chmod(wrapper, 0o750)
            (root / "operations/chatops/send_failback.sh").write_text(
                "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
            )
            guard = root / "operations/db/zabbix_failback_guard.sh"
            guard.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            os.chmod(guard, 0o750)
            compose = root / "docker-compose.yml"
            compose.write_text(
                f"- {wrapper}:/usr/lib/zabbix/alertscripts/send_failback.sh:ro\n"
                f"- {guard}:/usr/lib/zabbix/alertscripts/zabbix_failback_guard.sh:ro\n",
                encoding="utf-8",
            )
            checker = FinalLivePreflight(
                root=root,
                values={
                    "ZABBIX_COMPOSE_FILE": str(compose),
                    "ZABBIX_HOST_FAILBACK_WRAPPER": str(wrapper),
                },
                runner=lambda *_args, **_kwargs: (0, ""),
                include_legacy=False,
            )
            checker.check_zabbix_failback_runtime()
            self.assertEqual(checker.checks[-1].status, "PASS")

    def test_fixed_endpoint_accepts_expected_post_only_method_response(self):
        checker = FinalLivePreflight(
            values={"SLACK_FIXED_ENDPOINT_BASE_URL": "https://chatops.example"},
            http=lambda *_args, **_kwargs: (False, "HTTP_405"),
            include_legacy=False,
        )
        checker.check_fixed_slack_endpoint()
        self.assertEqual(checker.checks[-1].status, "PASS")

    def test_clock_sync_requires_synchronized_clock_and_active_daemon(self):
        def runner(command, **_kwargs):
            if command[0] == "timedatectl":
                return 0, "yes\nyes"
            if command[0:2] == ["systemctl", "is-active"]:
                return 0, "active"
            return 0, ""

        checker = FinalLivePreflight(
            runner=runner,
            include_legacy=False,
        )
        checker.check_clock_sync()
        self.assertEqual(checker.checks[-1].status, "PASS")

    def test_egress_workflow_rejects_retired_nat_gateway_lifecycle_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "operations/failover").mkdir(parents=True)
            (root / "operations/db").mkdir(parents=True)
            (root / "operations/failover/full_failover.sh").write_text(
                "#!/usr/bin/env bash\noperations/nat/nat_enable.sh\n", encoding="utf-8"
            )
            (root / "operations/db/db_failback_to_onprem.sh").write_text(
                "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
            )
            checker = FinalLivePreflight(root=root, include_legacy=False)
            checker.check_egress_workflow_contract()
            self.assertEqual(checker.checks[-1].status, "BLOCKED")
            self.assertIn("FAILOVER_EGRESS_MODE_UNRECONCILED", checker.blockers)

    def test_egress_workflow_accepts_web_nat_instance_only_guards(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "operations/failover").mkdir(parents=True)
            (root / "operations/db").mkdir(parents=True)
            (root / "operations/failover/full_failover.sh").write_text(
                "#!/usr/bin/env bash\noperations/nat_instance/web_nat_instance_status.sh\n",
                encoding="utf-8",
            )
            (root / "operations/db/db_failback_to_onprem.sh").write_text(
                "#!/usr/bin/env bash\noperations/nat_instance/web_nat_instance_status.sh\n",
                encoding="utf-8",
            )
            (root / "operations/failover/full_failback.sh").write_text(
                "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
            )
            checker = FinalLivePreflight(root=root, include_legacy=False)
            checker.check_egress_workflow_contract()
            self.assertEqual(checker.checks[-1].status, "PASS")

    def test_disabled_aws_wazuh_path_does_not_probe_stale_manager_endpoint(self):
        checker = FinalLivePreflight(
            values={
                "AWS_WAZUH_EXTERNAL_PATH_ENABLED": "false",
                "WAZUH_MANAGER_HOST": "1.209.148.228",
                "ONPREM_WEB_POWER_STATE": "POWERED_OFF",
                "ONPREM_EMR_POWER_STATE": "POWERED_OFF",
                "ONPREM_DB_POWER_STATE": "POWERED_OFF",
            },
            tcp=lambda *_args, **_kwargs: (False, "should not be probed"),
            include_legacy=False,
        )
        checker.check_onprem_path()
        statuses = {item.name: item.status for item in checker.checks}
        self.assertEqual(statuses["onprem_wazuh_path"], "DISABLED")
        self.assertNotIn("ONPREM_WAZUH_1514_BLOCKED", checker.blockers)
        self.assertNotIn("ONPREM_WAZUH_1515_BLOCKED", checker.blockers)

    def test_enabled_aws_wazuh_path_remains_fail_closed(self):
        checker = FinalLivePreflight(
            values={
                "AWS_WAZUH_EXTERNAL_PATH_ENABLED": "true",
                "WAZUH_MANAGER_HOST": "192.0.2.10",
                "ONPREM_WEB_POWER_STATE": "POWERED_OFF",
                "ONPREM_EMR_POWER_STATE": "POWERED_OFF",
                "ONPREM_DB_POWER_STATE": "POWERED_OFF",
            },
            tcp=lambda *_args, **_kwargs: (False, "connection refused"),
            include_legacy=False,
        )
        checker.check_onprem_path()
        statuses = {item.name: item.status for item in checker.checks}
        self.assertEqual(statuses["onprem_wazuh_1514"], "BLOCKED")
        self.assertEqual(statuses["onprem_wazuh_1515"], "BLOCKED")
        self.assertIn("ONPREM_WAZUH_1514_BLOCKED", checker.blockers)
        self.assertIn("ONPREM_WAZUH_1515_BLOCKED", checker.blockers)

    def test_chatops_state_read_is_fail_closed_for_active_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "chatops.sqlite3"
            connection = sqlite3.connect(db)
            connection.executescript(
                """
                CREATE TABLE approval_requests (status TEXT);
                CREATE TABLE emergency_console (status TEXT);
                INSERT INTO approval_requests(status) VALUES ('PENDING');
                INSERT INTO emergency_console(status) VALUES ('READY');
                """
            )
            connection.commit()
            connection.close()
            checker = FinalLivePreflight(
                values={"CHATOPS_STATE_DB": str(db)},
                runner=lambda *_args, **_kwargs: (1, ""),
                include_legacy=False,
            )
            checker.check_chatops_runtime_state()
            statuses = {item.name: item.status for item in checker.checks}
            self.assertEqual(statuses["chatops_state"], "BLOCKED")

    def test_slack_contract_requires_the_separate_emergency_app(self):
        checker = FinalLivePreflight(
            values={
                "SLACK_CHANNEL_ID": "C_ALERT",
                "SLACK_ALERT_CHANNEL_ID": "C_GENERAL",
                "SLACK_FAILOVER_BOT_TOKEN": "synthetic-failover",
                "SLACK_FAILBACK_BOT_TOKEN": "synthetic-failback",
            },
            include_legacy=False,
        )
        checker.check_slack()
        self.assertEqual(checker.checks[0].status, "BLOCKED")
        self.assertIn("SLACK_CONTRACT_MISSING", checker.blockers)

    def test_expected_standby_does_not_create_blocker_but_team_gate_does(self):
        result = assess_readiness(
            [
                Check("aws_state", "PASS", "SUSPENDED_EXPECTED"),
                Check("direct_nr", "BLOCKED", "NR not enabled", "DIRECT_NR_BLOCKED_TEAM_PFSENSE"),
                Check("approvers", "BLOCKED", "one approver", "BLOCKED_SECOND_APPROVER"),
            ],
            root=ROOT,
        )
        self.assertFalse(result["safe_to_start_live_e2e"])
        self.assertIn("DIRECT_NR_BLOCKED_TEAM_PFSENSE", result["blockers"])
        self.assertIn("BLOCKED_SECOND_APPROVER", result["team_dependencies"])
        self.assertEqual(result["aws_cost_change"], "NONE_READ_ONLY_PREFLIGHT")
        self.assertFalse(result["aws_instances_started"])
        self.assertFalse(result["aws_new_resources"])

    def test_ready_checks_can_produce_demo_ready_without_runtime_values(self):
        result = assess_readiness(
            [
                Check("repository", "PASS", "clean"),
                Check("aws_state", "PASS", "SUSPENDED_EXPECTED"),
            ],
            root=ROOT,
        )
        self.assertTrue(result["safe_to_start_live_e2e"])
        self.assertTrue(result["demo_ready"])

    def test_disabled_external_agent_paths_are_not_readiness_blockers(self):
        result = assess_readiness(
            [
                Check("aws_zabbix_external_path", "DISABLED", "disabled by design"),
                Check("aws_wazuh_external_path", "DISABLED", "disabled by design"),
                Check("msp_zabbix_ingress", "DISABLED", "not required"),
            ],
            root=ROOT,
        )
        self.assertTrue(result["safe_to_start_live_e2e"])
        self.assertTrue(result["demo_ready"])
        self.assertIn("aws_zabbix_external_path:disabled by design", result["environment_limitations"])
        self.assertEqual(result["blockers"], [])

    def test_summary_and_env_parser_never_emit_secret_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "dr.env"
            secret = "do-not-print-this-value"
            path.write_text(f"TOKEN={secret}\nDB_NR_ENABLED=false\n", encoding="utf-8")
            values = parse_env_file(path)
            self.assertEqual(values["TOKEN"], secret)
            summary = render_summary(
                assess_readiness([Check("secret", "PASS", "external file present")], root=ROOT)
            )
            self.assertNotIn(secret, summary)
            self.assertNotIn(secret, json.dumps(assess_readiness([Check("secret", "PASS", "external file present")], root=ROOT)))

    def test_sensitive_env_values_never_enter_preflight_json(self):
        synthetic = {
            "SESSION_SECRET": "synthetic-session-secret",
            "SLACK_FAILOVER_BOT_TOKEN": "synthetic-failover-token",
            "SLACK_FAILBACK_BOT_TOKEN": "synthetic-failback-token",
            "SLACK_ALERT_BOT_TOKEN": "synthetic-alert-token",
            "MYSQL_ROOT_PASS": "synthetic-mysql-password",
            "DB_REPL_PASSWORD": "synthetic-repl-password",
            "DB_BACKUP_ENCRYPTION_KEY": "synthetic-backup-key",
        }
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "dr.env"
            output_file = Path(temporary) / "preflight.json"
            env_file.write_text(
                "".join(f"{key}={value}\n" for key, value in synthetic.items()),
                encoding="utf-8",
            )
            parsed = parse_env_file(env_file)
            result = assess_readiness(
                [Check("repository", "PASS", "clean")],
                root=ROOT,
            )
            write_json(output_file, result)
            rendered = render_summary(result)
            serialized = output_file.read_text(encoding="utf-8")

        self.assertEqual(parsed["SESSION_SECRET"], synthetic["SESSION_SECRET"])
        for value in synthetic.values():
            self.assertNotIn(value, rendered)
            self.assertNotIn(value, serialized)

    def test_rpo_is_fail_closed_until_nr_is_enabled(self):
        evidence = {
            "source_log_file": "mysql-bin.000100",
            "source_log_pos": 100,
            "replica_log_file": "mysql-bin.000100",
            "replica_log_pos": 100,
            "seconds_behind_source": 0,
            "last_dump_at": "2026-09-20T00:00:00Z",
            "incident_at": "2026-09-20T00:10:00Z",
            "last_replicated_at": "2026-09-20T00:09:59Z",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rpo.json"
            path.write_text(json.dumps(evidence), encoding="utf-8")
            not_ready = collect(path, nr_enabled="false")
            ready = collect(path, nr_enabled="true")
        self.assertEqual(not_ready["rpo_status"], "NR_NOT_READY")
        self.assertFalse(not_ready["measured"])
        self.assertEqual(ready["rpo_status"], "MEASURED")
        self.assertTrue(ready["measured"])


if __name__ == "__main__":
    unittest.main()
