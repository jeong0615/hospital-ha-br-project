import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations" / "chatops"))

from rto_timeline import EVIDENCE_STAGES, STAGES, calculate_run, get_run, metric_values, record_stage, synthetic_run  # noqa: E402


class RtoTimelineTest(unittest.TestCase):
    def test_complete_timeline_calculates_all_durations_and_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rto.json"
            values = {
                "incident_at": "2026-09-19T00:00:00Z",
                "zabbix_detected_at": "2026-09-19T00:00:30Z",
                "failover_approval_created_at": "2026-09-19T00:01:00Z",
                "failover_approved_at": "2026-09-19T00:02:00Z",
                "failover_started_at": "2026-09-19T00:03:00Z",
                "service_recovered_at": "2026-09-19T00:10:00Z",
            }
            for stage in STAGES:
                record_stage("run-1", stage, timestamp=values[stage], path=path)

            result = get_run("run-1", path)
            self.assertEqual(result["detection_time_seconds"], 30.0)
            self.assertEqual(result["approval_creation_time_seconds"], 30.0)
            self.assertEqual(result["approval_time_seconds"], 60.0)
            self.assertEqual(result["approval_to_start_seconds"], 60.0)
            self.assertEqual(result["execution_time_seconds"], 420.0)
            self.assertEqual(result["total_rto_seconds"], 600.0)
            self.assertEqual(result["rto_target_result"], "PASS")

            metrics = metric_values(result)
            self.assertEqual(metrics["dr.rto.detection_seconds"], 30.0)
            self.assertEqual(metrics["dr.rto.approval_creation_seconds"], 30.0)
            self.assertEqual(metrics["dr.rto.approval_to_start_seconds"], 60.0)
            self.assertEqual(metrics["dr.rto.seconds"], 600.0)
            self.assertEqual(metrics["dr.rto.result"], 1)

    def test_missing_stage_is_not_reported_as_zero(self):
        result = calculate_run({"run_id": "partial", "target_seconds": 1800, "stages": {}})
        self.assertIsNone(result["total_rto_seconds"])
        self.assertEqual(result["rto_target_result"], "NOT_MEASURED")
        metrics = metric_values(result)
        self.assertNotIn("dr.rto.seconds", metrics)
        self.assertEqual(metrics["dr.rto.result"], -1)

    def test_duplicate_stage_is_idempotent_and_synthetic_run_is_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rto.json"
            first = record_stage("run-2", "incident_at", timestamp="2026-09-19T00:00:00Z", path=path)
            record_stage("run-2", "incident_at", timestamp="2026-09-19T01:00:00Z", path=path)
            self.assertEqual(first["stages"]["incident_at"], "2026-09-19T00:00:00Z")
            self.assertEqual(get_run("run-2", path)["stages"]["incident_at"], "2026-09-19T00:00:00Z")
            result = synthetic_run(path, "run-3")
            self.assertEqual(result["total_rto_seconds"], 600.0)
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual({run["run_id"] for run in document["runs"]}, {"run-2", "run-3"})

    def test_cli_accepts_runtime_path_after_subcommand(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rto.json"
            result = subprocess.run(
                [
                    "python3",
                    str(ROOT / "operations/chatops/rto_timeline.py"),
                    "record",
                    "--path",
                    str(path),
                    "--run-id",
                    "cli-run",
                    "--stage",
                    "incident_at",
                    "--at",
                    "2026-09-19T00:00:00Z",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"run_id": "cli-run"', result.stdout)

    def test_cli_metrics_emits_measured_values_without_zero_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rto.json"
            synthetic_run(path, "metrics-run")
            result = subprocess.run(
                [
                    "python3",
                    str(ROOT / "operations/chatops/rto_timeline.py"),
                    "metrics",
                    "--path",
                    str(path),
                    "--run-id",
                    "metrics-run",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("dr.rto.seconds=600.0", result.stdout)
            self.assertIn("dr.rto.result=1", result.stdout)

    def test_extended_evidence_stages_are_optional_and_traffic_end_is_measured(self):
        self.assertGreater(len(EVIDENCE_STAGES), len(STAGES))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rto.json"
            values = {
                "incident_at": "2026-09-19T00:00:00Z",
                "service_recovered_at": "2026-09-19T00:10:00Z",
                "dns_traffic_recovered_at": "2026-09-19T00:12:00Z",
            }
            for stage, timestamp in values.items():
                record_stage("evidence-run", stage, timestamp=timestamp, path=path)
            result = get_run("evidence-run", path)
            self.assertEqual(result["total_rto_seconds"], 720.0)
            self.assertEqual(result["traffic_recovery_time_seconds"], 120.0)
            self.assertEqual(metric_values(result)["dr.rto.traffic_recovery_seconds"], 120.0)


if __name__ == "__main__":
    unittest.main()
