import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "operations/db/s3_backup_upload.sh"
SERVICE = ROOT / "operations/db/systemd/hospital-dr-s3-upload.service"
TIMER = ROOT / "operations/db/systemd/hospital-dr-s3-upload.timer"
TERRAFORM = ROOT / "infra/terraform/main.tf"


FAKE_AWS = r"""#!/usr/bin/env bash
set -euo pipefail

printf '%q ' "$@" >> "$FAKE_AWS_LOG"
printf '\n' >> "$FAKE_AWS_LOG"

args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ "${args[i]}" == "--prefix" ]]; then
    prefix="${args[i + 1]}"
  fi
done

if [[ " $* " == *" s3api list-objects-v2 "* ]]; then
  while IFS= read -r candidate; do
    [[ -n "$candidate" && "$candidate" == "$prefix"* ]] && printf '%s\n' "$candidate"
  done < "$FAKE_S3_KEYS"
  exit 0
fi

if [[ " $* " == *" s3 cp "* ]]; then
  for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ "${args[i]}" == cp ]]; then
      printf '%s\n' "${args[i + 2]}" >> "$FAKE_UPLOADS"
      exit 0
    fi
  done
fi

echo "unexpected fake aws invocation" >&2
exit 90
"""


class S3BackupUploadContractTest(unittest.TestCase):
    def write_pair(self, backup_dir, name, status):
        (backup_dir / f"{name}.sql.gz.enc").write_bytes(b"encrypted backup")
        (backup_dir / f"{name}.master_status").write_text(status)

    def run_uploader(self, temp_dir, existing_keys=()):
        temp_dir = Path(temp_dir)
        backup_dir = temp_dir / "db_backups"
        bin_dir = temp_dir / "bin"
        backup_dir.mkdir(exist_ok=True)
        bin_dir.mkdir()
        fake_aws = bin_dir / "aws"
        fake_aws.write_text(FAKE_AWS)
        fake_aws.chmod(0o755)

        keys_file = temp_dir / "s3_keys"
        keys_file.write_text("\n".join(existing_keys) + "\n")
        aws_log = temp_dir / "aws.log"
        uploads = temp_dir / "uploads.log"
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "DR_SECRET_FILE": str(temp_dir / "missing-dr.env"),
                "S3_BACKUP_SOURCE_DIR": str(backup_dir),
                "S3_BACKUP_BUCKET": "hospital-dr-db-backup-test",
                "S3_BACKUP_PREFIX": "mysql/hospital_db",
                "S3_BACKUP_AWS_PROFILE": "dedicated-backup",
                "S3_BACKUP_LOCK_FILE": str(temp_dir / "upload.lock"),
                "FAKE_S3_KEYS": str(keys_file),
                "FAKE_AWS_LOG": str(aws_log),
                "FAKE_UPLOADS": str(uploads),
            }
        )
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        upload_names = uploads.read_text().splitlines() if uploads.exists() else []
        aws_calls = aws_log.read_text() if aws_log.exists() else ""
        return result, upload_names, aws_calls

    def test_new_and_legacy_status_formats_are_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            backup_dir = temp_dir / "db_backups"
            backup_dir.mkdir()
            self.write_pair(
                backup_dir,
                "new_format",
                "-- CHANGE MASTER TO MASTER_LOG_FILE='mysql-bin.000049', MASTER_LOG_POS=887;\n",
            )
            self.write_pair(
                backup_dir,
                "file_position",
                "File: mysql-bin.000050\nPosition: 901\n",
            )
            self.write_pair(
                backup_dir,
                "binlog_position",
                "BinlogFile: mysql-bin.000051\nBinlogPosition: 1200\n",
            )
            self.write_pair(
                backup_dir,
                "invalid",
                "File: mysql-bin.000052\nPosition: not-a-number\n",
            )
            (backup_dir / "incomplete.sql.gz.enc").write_bytes(b"encrypted backup")

            result, uploads, _ = self.run_uploader(temp_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                sorted(Path(destination).name for destination in uploads),
                sorted(
                    [
                        "new_format.sql.gz.enc",
                        "new_format.master_status",
                        "file_position.sql.gz.enc",
                        "file_position.master_status",
                        "binlog_position.sql.gz.enc",
                        "binlog_position.master_status",
                    ]
                ),
            )
            self.assertIn("Skipping invalid master_status pair: invalid.sql.gz.enc", result.stdout)
            self.assertIn("Skipping incomplete pair: incomplete.sql.gz.enc", result.stdout)

    def test_change_replication_source_multiline_status_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            backup_dir = temp_dir / "db_backups"
            backup_dir.mkdir()
            self.write_pair(
                backup_dir,
                "source_format",
                (
                    "-- CHANGE REPLICATION SOURCE TO\n"
                    "-- SOURCE_LOG_FILE='mysql-bin.000053',\n"
                    "-- SOURCE_LOG_POS=1337;\n"
                ),
            )
            self.write_pair(
                backup_dir,
                "source_one_line",
                (
                    "-- CHANGE REPLICATION SOURCE TO "
                    "SOURCE_LOG_FILE='mysql-bin.000054', SOURCE_LOG_POS=1444;\n"
                ),
            )

            result, uploads, _ = self.run_uploader(temp_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                sorted(Path(destination).name for destination in uploads),
                sorted(
                    [
                        "source_format.sql.gz.enc",
                        "source_format.master_status",
                        "source_one_line.sql.gz.enc",
                        "source_one_line.master_status",
                    ]
                ),
            )

    def test_exact_key_contract_and_partial_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            backup_dir = temp_dir / "db_backups"
            backup_dir.mkdir()
            status = "BinlogFile: mysql-bin.000049\nBinlogPosition: 887\n"
            for name in ("complete", "dump_only", "status_only", "prefix_collision"):
                self.write_pair(backup_dir, name, status)

            prefix = "mysql/hospital_db"
            existing = [
                f"{prefix}/complete.sql.gz.enc",
                f"{prefix}/complete.master_status",
                f"{prefix}/dump_only.sql.gz.enc",
                f"{prefix}/status_only.master_status",
                f"{prefix}/prefix_collision.sql.gz.enc.extra",
            ]
            result, uploads, aws_calls = self.run_uploader(temp_dir, existing)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                sorted(Path(destination).name for destination in uploads),
                sorted(
                    [
                        "dump_only.master_status",
                        "status_only.sql.gz.enc",
                        "prefix_collision.sql.gz.enc",
                        "prefix_collision.master_status",
                    ]
                ),
            )
            self.assertIn("s3api list-objects-v2", aws_calls)
            self.assertIn("Key==", aws_calls)
            self.assertIn("Pair already exists; skipping: complete.sql.gz.enc", result.stdout)

    def test_static_least_privilege_and_schedule_contract(self):
        source = SCRIPT.read_text()
        service = SERVICE.read_text()
        readme = (ROOT / "operations/db/systemd/README.md").read_text()
        timer = TIMER.read_text()
        terraform = TERRAFORM.read_text()
        forbidden_head_object = "head-" + "object"
        forbidden_get_object = "Get" + "Object"

        self.assertIn('BACKUP_DIR="${S3_BACKUP_SOURCE_DIR:-/home/cloud/db_backups}"', source)
        self.assertIn(
            "Environment=DR_SECRET_FILE=/home/cloud/hospital-dr-secrets/dr.env",
            service,
        )
        self.assertIn("/home/cloud/hospital-dr-secrets/dr.env", readme)
        self.assertIn("list-objects-v2", source)
        self.assertNotIn(forbidden_head_object, source)
        self.assertNotIn(forbidden_get_object, source)
        self.assertNotIn(f'"s3:{forbidden_get_object}"', terraform)
        self.assertIn("OnCalendar=*-*-* *:05,35:00", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("AccuracySec=30s", timer)


if __name__ == "__main__":
    unittest.main()
