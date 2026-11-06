import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "operations/db/failback_stability.sh"
GUARD = ROOT / "operations/db/zabbix_failback_guard.sh"
SENDER = ROOT / "operations/chatops/send_failback.sh"
COMPOSE = ROOT / "monitoring/zabbix/docker-compose.yml"


class FailbackStabilityTest(unittest.TestCase):
    def test_shared_policy_has_default_window_and_test_override(self):
        command = (
            f"source {HELPER}; "
            "failback_stability_validate_config 300 2 && "
            "! failback_stability_is_satisfied 100 399 300 && "
            "failback_stability_is_satisfied 100 400 300 && "
            "[ \"$(failback_stability_remaining 100 250 300)\" = 150 ]"
        )
        result = subprocess.run(["bash", "-c", command], cwd=ROOT, check=False)
        self.assertEqual(result.returncode, 0)

    def test_guard_is_fail_closed_outside_dr_active_without_network_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            log_dir = Path(temporary) / "logs"
            environment = os.environ.copy()
            environment.update(
                {
                    "DR_STATE_DIR": str(state_dir),
                    "DR_LOG_DIR": str(log_dir),
                    "DR_STATE_OWNER": "nobody:nogroup",
                    "SEND_FAILBACK": "/bin/true",
                }
            )
            result = subprocess.run(
                ["bash", str(GUARD)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("not DR_ACTIVE", (log_dir / "zabbix_failback_guard.log").read_text())

    def test_guard_requires_explicit_recovery_probe_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            log_dir = Path(temporary) / "logs"
            environment = os.environ.copy()
            for variable in ("ONPREM_WEB_HEALTH_URL", "ONPREM_DOC_HEALTH_URL", "ONPREM_DB_HOST"):
                environment.pop(variable, None)
            environment.update(
                {
                    "DR_STATE_DIR": str(state_dir),
                    "DR_LOG_DIR": str(log_dir),
                    "DR_STATE_OWNER": "nobody:nogroup",
                    "SEND_FAILBACK": "/bin/true",
                }
            )
            state_dir.mkdir(parents=True)
            (state_dir / "dr_state").write_text("DR_ACTIVE\n", encoding="utf-8")
            result = subprocess.run(
                ["bash", str(GUARD)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            log = (log_dir / "zabbix_failback_guard.log").read_text()
            self.assertIn("Recovery probe configuration is missing", log)

    def test_sender_wires_zabbix_recovery_to_guard_and_preserves_args(self):
        source = SENDER.read_text(encoding="utf-8")
        for marker in (
            "FAILBACK_STABILITY_GUARD_ENABLED",
            "FAILBACK_GUARD_SCRIPT",
            "FAILBACK_GUARD_BYPASS",
            'exec bash "$FAILBACK_GUARD_SCRIPT" "$@"',
        ):
            self.assertIn(marker, source)
        guard = GUARD.read_text(encoding="utf-8")
        for marker in (
            "FAILBACK_STABILITY_SECONDS",
            "FAILBACK_GUARD_TEST_MODE",
            "NETWORK_DR_HEALTH_URL",
            "probe_http",
            "probe_network_health",
            "recovery_started_at=\"\"",
            "stability timer reset",
            "command -v wget",
            "probe_tcp",
            "/dev/tcp/",
        ):
            self.assertIn(marker, guard)
        self.assertIn('bash "$SEND_FAILBACK" "${ORIGINAL_ARGS[@]}"', guard)

    def test_zabbix_compose_mounts_guard_policy_and_persistent_state(self):
        source = COMPOSE.read_text(encoding="utf-8")
        for marker in (
            "zabbix_failback_guard.sh",
            "failback_stability.sh",
            "/var/lib/hospital-dr:/var/lib/hospital-dr:rw",
            "DR_STATE_HELPER: /usr/lib/zabbix/dr_state.sh",
            "DR_STATE_FILE_MODE: \"0660\"",
            "DR_LOG_DIR: /var/lib/hospital-dr",
            "group_add:",
        ):
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
