import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ProvisioningPreflightContractTest(unittest.TestCase):
    def read(self, relative_path):
        return (ROOT / relative_path).read_text()

    def test_preflight_is_read_only_and_checks_required_contracts(self):
        source = self.read("operations/runtime/provisioning_preflight.sh")
        for marker in (
            "DR_SSH_PRIVATE_KEY",
            "TF_VAR_ssh_public_key_path",
            "DR_IMAGE_TAG",
            "DB_ALLOW_EXISTING_DATA",
            "DB_NR_ENABLED",
            "DB_PROMOTE_ON_FAILOVER",
            "DB_BACKUP_FILE",
            "DR_MANAGEMENT_IPV4_CIDRS",
            "TF_VAR_management_ipv4_cidrs",
            "MYSQL_ROOT_PASS",
            "WEB_DB_PASSWORD",
            "DOC_DB_PASSWORD",
            "SESSION_SECRET",
            "aws ecr describe-images",
            "terraform.tfstate",
            "AWS_REPROVISION_E2E_READY=false",
        ):
            self.assertIn(marker, source)
        for forbidden in (
            "aws ec2 start-instances",
            "aws ec2 stop-instances",
            "terraform apply",
            "terraform destroy",
            "ansible-playbook",
            "RESET REPLICA",
            "CHANGE REPLICATION SOURCE",
            "START REPLICA",
        ):
            self.assertNotIn(forbidden, source)

    def test_runtime_scripts_share_the_ssh_key_contract(self):
        runtime = self.read("operations/runtime_paths.sh")
        demo = self.read("infra/terraform/run_demo.sh")
        environment = self.read("operations/finops/dr_environment_control.sh")
        nat = self.read("operations/nat/nat_common.sh")
        terraform = self.read("infra/terraform/main.tf")
        self.assertIn("DR_SSH_PRIVATE_KEY", runtime)
        self.assertIn("DR_SSH_PRIVATE_KEY", demo)
        self.assertIn("DR_SSH_PRIVATE_KEY", environment)
        self.assertNotIn("require_nat_ssh_key", nat)
        self.assertNotIn("DR_SSH_PRIVATE_KEY", nat)
        self.assertIn("TF_VAR_ssh_public_key_path", environment)
        self.assertIn("NAT-only lifecycle operations", terraform)
        self.assertIn("ssh_public_key_path must be supplied for full DR EC2 provisioning", terraform)
        self.assertIn('default     = ""', terraform)
        self.assertIn('public_key = trimspace(var.ssh_public_key_path) == "" ? "" : file(var.ssh_public_key_path)', terraform)
        self.assertIn('require_env "SSH_KEY"', demo)
        self.assertIn('TF_VAR_ssh_public_key_path="${TF_VAR_ssh_public_key_path:-${SSH_KEY}.pub}"', demo)
        self.assertIn('if [ ! -r "$TF_VAR_ssh_public_key_path" ]', demo)
        self.assertNotIn('/etc/hospital-dr/ssh/dr_key.pub', terraform)
        self.assertNotIn("SSH_KEY=\"${SSH_KEY:-/etc/hospital-dr/ssh/dr_key}\"", environment)

    def test_full_provisioning_still_fails_before_terraform_without_ssh_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp_dir = Path(temporary)
            bin_dir = temp_dir / "bin"
            bin_dir.mkdir()
            (bin_dir / "aws").write_text("#!/bin/sh\nprintf 'synthetic-cloudflare-token\\n'\n")
            (bin_dir / "aws").chmod(0o755)
            terraform_calls = temp_dir / "terraform.calls"
            (bin_dir / "terraform").write_text(
                f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {terraform_calls}\nexit 0\n"
            )
            (bin_dir / "terraform").chmod(0o755)
            empty_secret = temp_dir / "empty.env"
            empty_secret.write_text("")
            state_dir = temp_dir / "state"
            env = os.environ.copy()
            for key in ("SSH_KEY", "DR_SSH_PRIVATE_KEY", "DR_SSH_KEY"):
                env.pop(key, None)
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "DR_REPO_ROOT": str(ROOT),
                    "DR_TERRAFORM_DIR": str(temp_dir / "terraform"),
                    "DR_ANSIBLE_SOURCE_DIR": str(ROOT / "automation/ansible"),
                    "DR_SECRET_FILE": str(empty_secret),
                    "DR_STATE_DIR": str(state_dir),
                    "DR_LOG_DIR": str(temp_dir / "logs"),
                    "CF_ZONE_ID": "0" * 32,
                    "TF_VAR_management_ipv4_cidrs": "10.0.0.0/8",
                    "DR_IMAGE_TAG": "a" * 40,
                    "INVENTORY": str(temp_dir / "inventory.ini"),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "infra/terraform/run_demo.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            output = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Required environment variable is missing: SSH_KEY", output)
            self.assertFalse(terraform_calls.exists(), output)

    def test_aws_ssh_paths_use_persistent_host_key_verification(self):
        runtime = self.read("operations/runtime_paths.sh")
        ansible = self.read("automation/ansible/ansible.cfg")
        demo = self.read("infra/terraform/run_demo.sh")
        environment = self.read("operations/finops/dr_environment_control.sh")
        self.assertIn("DR_SSH_KNOWN_HOSTS_FILE", runtime)
        self.assertIn("dr_prepare_ssh_known_hosts", runtime)
        self.assertIn("host_key_checking = True", ansible)
        for source in (ansible, demo, environment):
            self.assertIn("StrictHostKeyChecking=accept-new", source)
            self.assertNotIn("StrictHostKeyChecking=no", source)
            self.assertNotIn("UserKnownHostsFile=/dev/null", source)

    def test_known_hosts_helper_creates_restricted_persistent_file(self):
        runtime = ROOT / "operations/runtime_paths.sh"
        with tempfile.TemporaryDirectory() as temporary:
            known_hosts = Path(temporary) / "ssh" / "known_hosts"
            script = f"""set -euo pipefail
DR_STATE_DIR="{Path(temporary) / 'state'}"
DR_SSH_KNOWN_HOSTS_FILE="{known_hosts}"
source "{runtime}"
dr_prepare_ssh_known_hosts
test -f "$DR_SSH_KNOWN_HOSTS_FILE"
test "$(stat -c '%a' "$DR_SSH_KNOWN_HOSTS_FILE")" = 600
test "$(stat -c '%a' "$(dirname "$DR_SSH_KNOWN_HOSTS_FILE")")" = 700
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_management_cidrs_are_external_and_not_test_net_defaults(self):
        terraform = self.read("infra/terraform/main.tf")
        demo = self.read("infra/terraform/run_demo.sh")
        nat = self.read("operations/nat/nat_common.sh")
        self.assertIn('variable "management_ipv4_cidrs"', terraform)
        self.assertIn("default     = []", terraform)
        self.assertNotIn('default     = ["203.0.113.10/32"]', terraform)
        self.assertIn('require_env "TF_VAR_management_ipv4_cidrs"', demo)
        self.assertIn("DR_MANAGEMENT_IPV4_CIDRS", demo)
        self.assertIn("TF_VAR_management_ipv4_cidrs", nat)
        start = terraform.index('variable "management_ipv4_cidrs"')
        end = terraform.index('variable "cloudflare_ipv4_cidrs"')
        self.assertNotIn('default     = ["0.0.0.0/0"]', terraform[start:end])

    def test_chatops_service_uses_canonical_workflow_source(self):
        source = self.read("operations/chatops/systemd/hospital-dr-chatops.service")
        self.assertIn("DR_REPO_ROOT=/home/cloud/hospital-dr-codex", source)
        self.assertIn("DR_SCRIPT_ROOT=/home/cloud/hospital-dr-codex/infra/terraform", source)
        self.assertIn("DR_ANSIBLE_SOURCE_DIR=/home/cloud/hospital-dr-codex/automation/ansible", source)
        self.assertNotIn("DR_REPO_ROOT=/home/cloud/hospital-dr-repo", source)

    def test_chatops_service_uses_production_wsgi_runtime(self):
        source = self.read("operations/chatops/systemd/hospital-dr-chatops.service")
        app = self.read("operations/chatops/app.py")
        wsgi = self.read("operations/chatops/wsgi.py")
        self.assertIn("/usr/bin/python3 -m gunicorn", source)
        self.assertIn("--bind 127.0.0.1:5000", source)
        self.assertIn("--workers 1", source)
        self.assertIn("wsgi:app", source)
        self.assertNotIn("app.py", source.split("ExecStart=", 1)[1].splitlines()[0])
        self.assertNotIn("app.run(", app)
        self.assertIn("start_expiry_worker(app)", wsgi)
        self.assertIn("start_cleanup_worker(app)", wsgi)

    def test_runtime_paths_use_the_external_msp_secret_contract_by_default(self):
        source = self.read("operations/runtime_paths.sh")
        self.assertIn(
            'DR_SECRET_FILE="${DR_SECRET_FILE:-/home/cloud/hospital-dr-secrets/dr.env}"',
            source,
        )
        self.assertIn(
            'DR_STATE_DIR="${DR_STATE_DIR:-/var/lib/hospital-dr}"',
            source,
        )
        self.assertIn(
            'DR_TLS_CERT_FILE="${DR_TLS_CERT_FILE:-/home/cloud/hospital-dr-secrets/tls/hospital-web.crt}"',
            source,
        )
        self.assertIn(
            'DR_TLS_KEY_FILE="${DR_TLS_KEY_FILE:-/home/cloud/hospital-dr-secrets/tls/hospital-web.key}"',
            source,
        )
        self.assertIn('REQUIRE_PRODUCTION_TLS="${REQUIRE_PRODUCTION_TLS:-true}"', source)

    def test_provisioning_entrypoint_loads_optional_external_secret_file(self):
        source = self.read("infra/terraform/run_demo.sh")
        self.assertIn("DR_PROVISIONING_SECRET_FILE", source)
        self.assertIn("does not exist", source)

    def test_provisioning_uses_canonical_ansible_source_with_external_inventory(self):
        source = self.read("infra/terraform/run_demo.sh")
        self.assertIn('ANSIBLE_SOURCE_DIR="${DR_ANSIBLE_SOURCE_DIR:-$DR_REPO_ROOT/automation/ansible}"', source)
        self.assertIn('INVENTORY="${INVENTORY:-$ANSIBLE_DIR/inventory.ini}"', source)
        self.assertIn('export ANSIBLE_CONFIG="${ANSIBLE_CONFIG:-$ANSIBLE_SOURCE_DIR/ansible.cfg}"', source)
        self.assertIn('cd "$ANSIBLE_SOURCE_DIR"', source)
        self.assertNotIn('cd "$ANSIBLE_DIR"', source)

    def test_preflight_does_not_require_the_legacy_opt_layout(self):
        source = self.read("operations/runtime/provisioning_preflight.sh")
        self.assertIn("/opt/hospital-dr is not required", source)
        self.assertIn("DR_REPO_ROOT", source)

    def test_preflight_extracts_inventory_key_value_without_first_equals_false_positive(self):
        source = self.read("operations/runtime/provisioning_preflight.sh")
        self.assertIn(
            "grep -oE 'ansible_ssh_private_key_file=[^[:space:]]+' \"$INVENTORY\" | cut -d= -f2",
            source,
        )
        self.assertNotIn(
            "awk -F= '/ansible_ssh_private_key_file=/{print $2}'",
            source,
        )

    def test_preflight_reports_runtime_source_state_and_tls_status(self):
        source = self.read("operations/runtime/provisioning_preflight.sh")
        for marker in (
            "RUNTIME_DRIFT_CHECK",
            "runtime_drift_check.sh",
            "SECRETS_READY=",
            "RUNTIME_SOURCE_READY=",
            "TERRAFORM_SOURCE_READY=",
            "TERRAFORM_STATE_READY=",
            "FRESH_HOST_READY=",
            "TLS_STATUS=",
            "AWS_REPROVISION_E2E_PREPARED=",
            "AWS_REPROVISION_E2E_READY=false",
        ):
            self.assertIn(marker, source)

    def test_runtime_drift_check_is_read_only_and_excludes_sensitive_artifacts(self):
        source = self.read("operations/runtime/runtime_drift_check.sh")
        for marker in (
            "--check",
            "cmp -s",
            "RUNTIME_SOURCE_READY=false",
            "/home/cloud/dr_project",
            "RUNTIME_SOURCE_REPO=\"${DR_RUNTIME_SOURCE_REPO:-$CANONICAL_ROOT}\"",
            "/etc/systemd/system",
        ):
            self.assertIn(marker, source)
        self.assertNotIn(
            'RUNTIME_SOURCE_REPO="${DR_RUNTIME_SOURCE_REPO:-/home/cloud/hospital-dr-repo}"',
            source,
        )
        for forbidden in ("cp ", "mv -f", "rm -f", "terraform apply", "rsync --delete"):
            self.assertNotIn(forbidden, source)

    def test_terraform_runtime_sync_preserves_state_and_has_explicit_modes(self):
        source = self.read("operations/runtime/sync_terraform_runtime.sh")
        for marker in (
            "--check|--apply",
            "terraform.tfstate",
            "terraform.tfstate.*",
            ".terraform/*",
            "terraform -chdir=",
            "TERRAFORM_SOURCE_READY=true",
            "TERRAFORM_SOURCE_READY=false",
        ):
            self.assertIn(marker, source)
        for forbidden in ("rsync --delete", "terraform apply", "terraform destroy", "state rm", "state mv"):
            self.assertNotIn(forbidden, source)

    def test_preflight_keeps_canonical_terraform_source_separate_from_runtime_state(self):
        source = self.read("operations/runtime/provisioning_preflight.sh")
        self.assertIn(
            'CANONICAL_TF_DIR="${DR_CANONICAL_TERRAFORM_DIR:-$DR_REPO_ROOT/infra/terraform}"',
            source,
        )
        self.assertIn(
            'TERRAFORM_RUNTIME_DIR="${DR_TERRAFORM_RUNTIME_DIR:-/home/cloud/hospital-dr-terraform}"',
            source,
        )
        self.assertNotIn('TF_DIR="${TF_DIR:-$DR_TERRAFORM_DIR}"', source)

    def test_lifecycle_systemd_templates_use_canonical_runtime_paths(self):
        for relative_path in (
            "infra/terraform/systemd/hospital-dr-environment.service",
            "infra/terraform/systemd/hospital-dr-nat-disable.service",
            "infra/terraform/systemd/hospital-dr-nat-enable.service",
            "infra/terraform/systemd/hospital-dr-nat-shutdown.service",
        ):
            source = self.read(relative_path)
            self.assertNotIn("/opt/hospital-dr", source)
            self.assertIn("/home/cloud/hospital-dr-codex", source)
            if relative_path.endswith("environment.service") or relative_path.endswith("shutdown.service"):
                self.assertIn("DR_SECRET_FILE=/home/cloud/hospital-dr-secrets/dr.env", source)

    def test_runtime_drift_check_detects_without_writing(self):
        script = ROOT / "operations/runtime/runtime_drift_check.sh"
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            canonical = base / "canonical"
            chatops = canonical / "operations" / "chatops"
            finops = canonical / "operations" / "finops"
            chatops_systemd = canonical / "operations" / "chatops" / "systemd"
            db_systemd = canonical / "operations" / "db" / "systemd"
            terraform_systemd = canonical / "infra" / "terraform" / "systemd"
            grafana = canonical / "monitoring" / "grafana" / "provisioning" / "dashboards" / "json"
            for directory in (chatops, finops, chatops_systemd, db_systemd, terraform_systemd, grafana):
                directory.mkdir(parents=True)
            (canonical / "operations" / "db" / "zabbix_failback_guard.sh").write_text(
                "#!/bin/bash\n"
            )

            for name in (
                "app.py",
                "wsgi.py",
                "chatops_audit.py",
                "chatops_core.py",
                "wazuh_alerts.py",
                "wazuh_correlation.py",
                "wazuh_failback_gate.py",
                "wazuh_live_provider.py",
                "rto_timeline.py",
                "zabbix_alerts.py",
                "zabbix_host_proxy.py",
            ):
                (chatops / name).write_text(f"# {name}\n")
            for name in ("send_slack.sh", "send_failback.sh"):
                (chatops / name).write_text(f"#!/bin/bash\n# {name}\n")
            (finops / "dr_environment_control.sh").write_text("#!/bin/bash\n")
            (chatops_systemd / "hospital-dr-chatops.service").write_text("[Unit]\n")
            (db_systemd / "hospital-dr-db-backup.service").write_text("[Unit]\n")
            (db_systemd / "hospital-dr-db-backup.timer").write_text("[Timer]\n")
            (terraform_systemd / "hospital-dr-environment.service").write_text("[Unit]\n")

            runtime_chatops = base / "runtime-chatops"
            runtime_finops = base / "runtime-finops"
            runtime_systemd = base / "runtime-systemd"
            runtime_source = base / "runtime-source"
            runtime_zabbix_alerts = base / "runtime-zabbix-alerts"
            runtime_grafana = base / "runtime-grafana"
            (canonical / "operations" / "runtime").mkdir()
            (canonical / "operations" / "failover").mkdir()
            (canonical / "operations" / "runtime_paths.sh").write_text("# runtime paths\n")
            (canonical / "operations" / "runtime" / "dr_state.sh").write_text("# state\n")
            (canonical / "operations" / "failover" / "dns_transaction.sh").write_text("# dns\n")
            (canonical / "operations" / "cloudflare_ssm_auth.sh").write_text("# cloudflare\n")
            for source in (
                chatops,
                finops,
                chatops_systemd,
                db_systemd,
                terraform_systemd,
            ):
                target = {
                    chatops: runtime_chatops,
                    finops: runtime_finops,
                    chatops_systemd: runtime_systemd,
                    db_systemd: runtime_systemd,
                    terraform_systemd: runtime_systemd,
                }[source]
                target.mkdir(parents=True, exist_ok=True)
                for source_file in source.iterdir():
                    if not source_file.is_file():
                        continue
                    (target / source_file.name).write_text(source_file.read_text())
            runtime_zabbix_alerts.mkdir(parents=True, exist_ok=True)
            for name in ("send_slack.sh", "send_failback.sh"):
                (runtime_zabbix_alerts / name).write_text((chatops / name).read_text())
            (base / "runtime-zabbix-failback-guard.sh").write_text(
                (canonical / "operations" / "db" / "zabbix_failback_guard.sh").read_text()
            )
            (grafana / "hospital-infrastructure.json").write_text('{"title":"test"}\n')
            runtime_grafana.mkdir(parents=True, exist_ok=True)
            (runtime_grafana / "hospital-infrastructure.json").write_text(
                (grafana / "hospital-infrastructure.json").read_text()
            )

            environment = os.environ.copy()
            environment.update(
                {
                    "DR_REPO_ROOT": str(canonical),
                    "DR_RUNTIME_SOURCE_REPO": str(runtime_source),
                    "DR_RUNTIME_CHATOPS_DIR": str(runtime_chatops),
                    "DR_RUNTIME_FINOPS_DIR": str(runtime_finops),
                    "DR_RUNTIME_SYSTEMD_DIR": str(runtime_systemd),
                    "DR_RUNTIME_ZABBIX_ALERTS_DIR": str(runtime_zabbix_alerts),
                    "DR_RUNTIME_ZABBIX_GUARD_FILE": str(base / "runtime-zabbix-failback-guard.sh"),
                    "DR_RUNTIME_GRAFANA_DASHBOARD_DIR": str(runtime_grafana),
                }
            )
            for relative in (
                "operations/runtime_paths.sh",
                "operations/runtime/dr_state.sh",
                "operations/failover/dns_transaction.sh",
                "operations/cloudflare_ssm_auth.sh",
            ):
                target = runtime_source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text((canonical / relative).read_text())
            matched = subprocess.run(
                [str(script), "--check"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(matched.returncode, 0, matched.stdout + matched.stderr)

            runtime_file = runtime_chatops / "chatops_core.py"
            original = runtime_file.read_text()
            runtime_file.write_text("# drift\n")
            drifted = subprocess.run(
                [str(script), "--check"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(drifted.returncode, 0)
            self.assertEqual(runtime_file.read_text(), "# drift\n")
            self.assertEqual((chatops / "chatops_core.py").read_text(), original)

    def test_terraform_sync_copies_allowlist_and_preserves_state(self):
        script = ROOT / "operations/runtime/sync_terraform_runtime.sh"
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            canonical = base / "canonical"
            runtime = base / "runtime"
            state = base / "state"
            manifest = (
                "main.tf",
                "github_oidc.tf",
                "iam_least_privilege_candidates.tf",
                ".terraform.lock.hcl",
                "lambda/dr_cost_guard.py",
                "iam-policies/HospitalDRSecretReadPolicy.json",
                "iam-policies/HospitalDRTerraformIAMPolicy.json",
                "iam-policies/HospitalDRTerraformProvisionerPolicy.json",
                "iam-policies/HospitalDRTerraformServerlessPolicy.json",
            )
            for relative_path in manifest:
                source_file = canonical / relative_path
                source_file.parent.mkdir(parents=True, exist_ok=True)
                source_file.write_text(f"canonical: {relative_path}\n")
            runtime.mkdir()
            state.mkdir()
            state_file = runtime / "terraform.tfstate"
            state_file.write_text("existing state must remain byte-for-byte unchanged\n")
            (runtime / "main.tf").write_text("old source\n")

            fake_bin = base / "bin"
            fake_bin.mkdir()
            fake_terraform = fake_bin / "terraform"
            fake_terraform.write_text("#!/bin/sh\nexit 0\n")
            fake_terraform.chmod(0o755)

            environment = os.environ.copy()
            environment.update(
                {
                    "DR_REPO_ROOT": str(base / "canonical-repo"),
                    "DR_CANONICAL_TERRAFORM_DIR": str(canonical),
                    "DR_TERRAFORM_RUNTIME_DIR": str(runtime),
                    "DR_STATE_DIR": str(state),
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                }
            )
            checked = subprocess.run(
                [str(script), "--check"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(checked.returncode, 0)
            self.assertEqual(state_file.read_text(), "existing state must remain byte-for-byte unchanged\n")

            applied = subprocess.run(
                [str(script), "--apply"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            self.assertEqual(state_file.read_text(), "existing state must remain byte-for-byte unchanged\n")
            self.assertEqual((runtime / "main.tf").read_text(), "canonical: main.tf\n")
            self.assertTrue((state / "terraform-sync-backups" / "main.tf").is_file())
            self.assertFalse((runtime / "terraform.tfstate").read_text() == "canonical: terraform.tfstate\n")

            final_check = subprocess.run(
                [str(script), "--check"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(final_check.returncode, 0, final_check.stdout + final_check.stderr)

    def test_terraform_drift_preflight_is_explicit_and_read_only(self):
        source = self.read("operations/runtime/provisioning_preflight.sh")
        self.assertIn('TERRAFORM_DRIFT_HELPER="$SCRIPT_DIR/terraform_drift_check.sh"', source)
        self.assertIn("DR_TERRAFORM_PLAN_JSON", source)
        self.assertIn("KEY_PAIR_MISMATCH", self.read("operations/runtime/terraform_drift_check.sh"))
        self.assertIn("MANAGEMENT_CIDR_NEEDS_INPUT", self.read("operations/runtime/terraform_drift_check.sh"))
        self.assertIn("TERRAFORM_DRIFT_CHECK=", source)


if __name__ == "__main__":
    unittest.main()
