import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/dr/dr_dry_run.py"


class DrDryRunTest(unittest.TestCase):
    def test_dry_run_uses_real_chatops_engine_without_external_side_effects(self):
        result = subprocess.run(
            ["python3", str(SCRIPT)], cwd=ROOT, text=True, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for marker in (
            "PROBLEM_EVENT=PASS",
            "GENERAL_ALERT=PASS",
            "FAILOVER_APPROVAL=PASS",
            "FAILOVER_EXECUTION=SKIPPED_DRY_RUN",
            "RECOVERY_EVENT=PASS",
            "NETWORK_DR_HEALTH=PASS",
            "RECOVERY_STABILITY_GUARD=PASS",
            "FAILBACK_APPROVAL=PASS",
            "FAILBACK_EXECUTION=SKIPPED_DRY_RUN",
            "RTO_METRICS=PASS",
            "GRAFANA_RTO_DATA=PASS",
            "DR_DRY_RUN_RESULT=PASS",
        ):
            self.assertIn(marker, result.stdout)

    def test_rto_dashboard_declares_all_stage_metrics_and_explicit_nr_status(self):
        dashboard = (ROOT / "monitoring/grafana/provisioning/dashboards/json/hospital-dr-rto.json").read_text()
        for metric in (
            "dr.rto.seconds",
            "dr.rto.target_seconds",
            "dr.rto.result",
            "dr.rto.detection_seconds",
            "dr.rto.approval_creation_seconds",
            "dr.rto.approval_seconds",
            "dr.rto.approval_to_start_seconds",
            "dr.rto.execution_seconds",
        ):
            self.assertIn(metric, dashboard)
        self.assertIn("RPO STATUS: NR NOT READY", dashboard)


if __name__ == "__main__":
    unittest.main()
