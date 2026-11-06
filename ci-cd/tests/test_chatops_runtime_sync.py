import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations" / "runtime" / "sync_chatops_runtime.sh"


class ChatOpsRuntimeSyncContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_uses_only_approved_source_and_runtime_paths(self):
        self.assertIn('CANONICAL_SOURCE_DIR="/home/cloud/hospital-dr-codex/operations/chatops"', self.source)
        self.assertIn('CANONICAL_RUNTIME_DIR="/home/cloud/dr_project"', self.source)
        self.assertIn('[[ "$SOURCE_DIR" == "$CANONICAL_SOURCE_DIR" ]]', self.source)
        self.assertIn('[[ "$RUNTIME_DIR" == "$CANONICAL_RUNTIME_DIR" ]]', self.source)

    def test_sync_allowlist_excludes_secrets_and_state(self):
        allowlist = self.source.split("SYNC_FILES=(", 1)[1].split(")", 1)[0]
        for file_name in (
            "chatops_core.py",
            "chatops_audit.py",
            "emergency_dr.py",
            "wazuh_alerts.py",
            "rto_timeline.py",
            "zabbix_alerts.py",
        ):
            self.assertIn(f"    {file_name}", allowlist)
        for forbidden in (
            "dr.env",
            "chatops_state.sqlite3",
            "chatops_audit.jsonl",
            "SLACK_",
            "private key",
        ):
            self.assertNotIn(forbidden, allowlist)

    def test_dry_run_and_restart_are_explicit(self):
        self.assertIn("--dry-run", self.source)
        self.assertIn("--restart", self.source)
        self.assertIn("--restart cannot be combined with --dry-run", self.source)
        self.assertIn('"--restart requires root; use sudo"', self.source)
        self.assertIn('SERVICE_NAME="hospital-dr-chatops.service"', self.source)
        self.assertIn('systemctl restart "$SERVICE_NAME"', self.source)
        self.assertIn("runtime py_compile failed; restored backup", self.source)

    def test_root_validation_uses_systemd_service_user_environment(self):
        self.assertIn('systemctl show --property=User --value "$SERVICE_NAME"', self.source)
        self.assertIn('runuser -u "$VALIDATION_USER" -- "$@"', self.source)
        self.assertIn('env -u PYTHONNOUSERSITE', self.source)
        self.assertIn('VALIDATION_PYTHON="/usr/bin/python3"', self.source)
        self.assertIn('VALIDATION_HOME=', self.source)
        self.assertIn('CHATOPS_VALIDATION_USER=%s', self.source)

    def test_validation_imports_actual_runtime_entrypoint_modules(self):
        for module_name in (
            "import flask",
            "import chatops_audit",
            "import emergency_dr",
            "import wazuh_alerts",
            "import chatops_core",
            "import rto_timeline",
            "import zabbix_alerts",
            "import wsgi",
        ):
            self.assertIn(module_name, self.source)

    def test_failed_validation_precedes_restart_and_dry_run_precedes_copy(self):
        validation_failure = self.source.index('fail "runtime import validation failed; restored backup"')
        restart = self.source.index('systemctl restart "$SERVICE_NAME"')
        self.assertLess(validation_failure, restart)

        dry_run_pass = self.source.index('printf \'CHATOPS_SYNC=DRY_RUN_PASS\\n\'')
        install = self.source.index('install -m 0644')
        self.assertLess(dry_run_pass, install)
        self.assertIn('if [[ "$DRY_RUN" == true ]]; then', self.source)
        self.assertIn('printf \'CHATOPS_SYNC=PASS\\n\'', self.source)


if __name__ == "__main__":
    unittest.main()
