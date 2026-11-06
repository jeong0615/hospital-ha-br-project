import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "operations" / "preflight"))

from rto_rpo_evidence_collector import collect_evidence, main, write_bundle  # noqa: E402


BASE = "2026-10-04T00:00:00Z"
BASE_EPOCH = datetime(2026, 10, 4, tzinfo=timezone.utc).timestamp()


def timestamp(offset):
    return datetime.fromtimestamp(BASE_EPOCH + offset, timezone.utc).isoformat().replace("+00:00", "Z")


def write_timeline(path, run_id="run-complete", incident_id="INC-001", *, complete=True, duplicate=False, invalid_order=False):
    incident = timestamp(0)
    service = timestamp(-10 if invalid_order else 900)
    stages = {
        "incident_at": {"at": incident, "event_id": incident_id},
        "zabbix_detected_at": {"at": timestamp(10), "event_id": incident_id},
        "failover_approval_created_at": {"at": timestamp(60), "approval_id": "req-1"},
        "first_approval_at": {"at": timestamp(120), "approval_id": "req-1"},
        "second_approval_at": {"at": timestamp(180), "approval_id": "req-1"},
        "failover_started_at": {"at": timestamp(200), "approval_id": "req-1"},
    }
    if complete:
        stages.update(
            {
                "service_recovered_at": {"at": service, "event_id": incident_id},
                "dns_traffic_recovered_at": {"at": timestamp(1000), "event_id": incident_id},
            }
        )
    runs = [{"run_id": run_id, "stages": stages}]
    if duplicate:
        runs.append({"run_id": run_id, "stages": stages})
    path.write_text(json.dumps({"version": 1, "runs": runs}), encoding="utf-8")


def write_state(path, run_id="run-complete", incident_id="INC-001"):
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE approval_requests (
                request_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                slack_client_key TEXT,
                status TEXT NOT NULL,
                incidents TEXT NOT NULL,
                approvers TEXT NOT NULL,
                created_at REAL NOT NULL,
                first_approved_at REAL,
                expires_at REAL NOT NULL,
                slack_channel_id TEXT,
                slack_message_ts TEXT,
                finished_at REAL,
                cancelled_by TEXT,
                result TEXT,
                execution_started_at REAL,
                execution_heartbeat_at REAL,
                execution_lease_until REAL,
                execution_id TEXT
            )
            """
        )
        incident = {
            "incident_id": incident_id,
            "rto_run_id": run_id,
            "source": "zabbix",
            "incident_at": timestamp(0),
            "observed_at": timestamp(10),
        }
        connection.execute(
            "INSERT INTO approval_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "req-1",
                "failover",
                "failover",
                "SUCCEEDED",
                json.dumps([incident]),
                json.dumps(["U1", "U2"]),
                BASE_EPOCH + 60,
                BASE_EPOCH + 120,
                BASE_EPOCH + 900,
                "C123",
                "1710000000.000001",
                BASE_EPOCH + 1000,
                None,
                "success",
                BASE_EPOCH + 200,
                BASE_EPOCH + 200,
                BASE_EPOCH + 210,
                "exec-1",
            ),
        )


def write_timing(path, run_id="run-complete", incident_id="INC-001"):
    records = [
        {"run_id": run_id, "incident_id": incident_id, "phase": "terraform", "event": "started_at", "value": BASE_EPOCH + 250},
        {"run_id": run_id, "incident_id": incident_id, "phase": "terraform", "event": "finished_at", "value": BASE_EPOCH + 300},
        {"run_id": run_id, "incident_id": incident_id, "phase": "ansible", "event": "started_at", "value": BASE_EPOCH + 350},
        {"run_id": run_id, "incident_id": incident_id, "phase": "ansible", "event": "finished_at", "value": BASE_EPOCH + 400},
        # Same event/timestamp is a harmless duplicate and must not change RTO.
        {"run_id": run_id, "incident_id": incident_id, "phase": "terraform", "event": "finished_at", "value": BASE_EPOCH + 300},
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


class RtoRpoEvidenceCollectorTest(unittest.TestCase):
    def test_complete_event_collects_real_timeline_and_healthy_nr(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            timeline = root / "timeline.json"
            state = root / "chatops.sqlite3"
            audit = root / "audit.jsonl"
            timing = root / "timing.jsonl"
            rpo = root / "rpo.json"
            write_timeline(timeline)
            write_state(state)
            write_timing(timing)
            audit.write_text(
                json.dumps({"event": "chatops_approval_request", "request_id": "req-1", "timestamp": BASE_EPOCH + 60}) + "\n",
                encoding="utf-8",
            )
            rpo.write_text(
                json.dumps(
                    {
                        "source_log_file": "mysql-bin.000100",
                        "source_log_pos": 100,
                        "replica_log_file": "mysql-bin.000100",
                        "replica_log_pos": 100,
                        "seconds_behind_source": 0,
                        "last_dump_at": timestamp(-3600),
                        "incident_at": timestamp(0),
                        "last_replicated_at": timestamp(-1),
                        "replica_io_running": "Yes",
                        "replica_sql_running": "Yes",
                        "last_test_transaction_id": "tx-001",
                        "checksum": "sha256:abc",
                        "not_allowed_secret": "must-not-appear",
                    }
                ),
                encoding="utf-8",
            )
            result = collect_evidence(
                run_id="run-complete",
                incident_id="INC-001",
                timeline_path=timeline,
                state_db_path=state,
                audit_path=audit,
                timing_path=timing,
                rpo_input_path=rpo,
                db_nr_enabled="true",
            )
            self.assertEqual(result["summary"]["rto_seconds"], 1000.0)
            self.assertEqual(result["summary"]["rto_status"], "PASS")
            self.assertEqual(result["summary"]["rpo_status"], "MEASURED")
            self.assertEqual(result["summary"]["replication_health"], "PASS")
            self.assertEqual(result["rto"]["events"]["terraform_started_at"]["source"], "timing:terraform:started_at")
            self.assertTrue(result["rto"]["duplicate_events"])
            self.assertNotIn("must-not-appear", json.dumps(result))

            evidence_dir = write_bundle(result, root / "evidence")
            self.assertEqual(
                {path.name for path in evidence_dir.iterdir()},
                {"summary.json", "rto_timeline.json", "rpo_evidence.json", "evidence_manifest.txt"},
            )
            self.assertIn("SHA256=", (evidence_dir / "evidence_manifest.txt").read_text(encoding="utf-8"))

    def test_incomplete_event_is_not_measured_and_does_not_invent_end_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            timeline = Path(temporary) / "timeline.json"
            write_timeline(timeline, complete=False)
            result = collect_evidence(
                run_id="run-complete",
                incident_id="INC-001",
                timeline_path=timeline,
                db_nr_enabled="false",
            )
            self.assertIsNone(result["summary"]["rto_seconds"])
            self.assertEqual(result["summary"]["rto_status"], "NOT_MEASURED")
            self.assertEqual(result["summary"]["rpo_status"], "NOT_MEASURABLE")
            self.assertIsNone(result["rto"]["events"]["traffic_recovered_at"])

    def test_missing_timestamp_is_not_replaced_with_current_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            timeline = Path(temporary) / "timeline.json"
            write_timeline(timeline)
            document = json.loads(timeline.read_text(encoding="utf-8"))
            document["runs"][0]["stages"]["service_recovered_at"]["at"] = None
            document["runs"][0]["stages"]["dns_traffic_recovered_at"]["at"] = None
            timeline.write_text(json.dumps(document), encoding="utf-8")
            result = collect_evidence(
                run_id="run-complete",
                incident_id="INC-001",
                timeline_path=timeline,
            )
            self.assertEqual(result["summary"]["rto_status"], "NOT_MEASURED")
            self.assertIsNone(result["summary"]["rto_seconds"])

    def test_duplicate_run_id_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as temporary:
            timeline = Path(temporary) / "timeline.json"
            write_timeline(timeline, duplicate=True)
            result = collect_evidence(
                run_id="run-complete",
                incident_id="INC-001",
                timeline_path=timeline,
            )
            self.assertEqual(result["summary"]["rto_status"], "NOT_MEASURED")
            self.assertIn("DUPLICATE_RUN_ID", result["summary"]["validation_errors"])

    def test_invalid_clock_order_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            timeline = Path(temporary) / "timeline.json"
            write_timeline(timeline, invalid_order=True)
            result = collect_evidence(
                run_id="run-complete",
                incident_id="INC-001",
                timeline_path=timeline,
            )
            self.assertEqual(result["summary"]["rto_status"], "NOT_MEASURED")
            self.assertTrue(any(error.startswith("INVALID_CLOCK_ORDER") for error in result["summary"]["validation_errors"]))

    def test_db_nr_false_is_not_measurable_even_if_input_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            rpo = Path(temporary) / "rpo.json"
            rpo.write_text(json.dumps({"source_log_file": "mysql-bin.000001", "secret": "x"}), encoding="utf-8")
            result = collect_evidence(
                run_id="run-rpo-off",
                incident_id="INC-002",
                rpo_input_path=rpo,
                db_nr_enabled="false",
            )
            self.assertEqual(result["summary"]["rpo_status"], "NOT_MEASURABLE")
            self.assertEqual(result["summary"]["replication_health"], "NOT_ENABLED")
            self.assertNotIn("secret", json.dumps(result["rpo"]))

    def test_cli_prints_only_presentation_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            timeline = root / "timeline.json"
            output = io.StringIO()
            write_timeline(timeline)
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--run-id",
                        "run-complete",
                        "--incident-id",
                        "INC-001",
                        "--timeline",
                        str(timeline),
                        "--output-root",
                        str(root / "evidence"),
                        "--db-nr-enabled",
                        "false",
                    ]
                )
            self.assertEqual(exit_code, 0)
            lines = output.getvalue().splitlines()
            self.assertIn("RTO_SECONDS=1000.0", lines)
            self.assertIn("RTO_STATUS=PASS", lines)
            self.assertIn("RPO_STATUS=NOT_MEASURABLE", lines)
            self.assertTrue(all("timestamp" not in line.lower() for line in lines))


if __name__ == "__main__":
    unittest.main()
