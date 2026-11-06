import re
import stat
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "operations/runtime/final_dr_preflight.sh"
RUNBOOK = ROOT / "docs/runbooks/FINAL_DR_INTEGRATION_AND_DEEP_FREEZE.md"


class FinalDRPreflightContractTest(unittest.TestCase):
    def read(self, path):
        return path.read_text(encoding="utf-8")

    def test_checker_is_executable_and_supports_help(self):
        mode = SCRIPT.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)
        result = subprocess.run(
            [str(SCRIPT), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("readiness checks", result.stdout)

    def test_checker_covers_final_readiness_domains(self):
        source = self.read(SCRIPT)
        for marker in (
            "GIT_HEAD_MATCHES_ORIGIN_MAIN",
            "GITHUB_ACTIONS_GREEN_RUN_",
            "IMMUTABLE_IMAGE_TAG_IS_CURRENT_HEAD",
            "ECR_WEB_REPOSITORY",
            "CHATOPS_SERVICE",
            "GUNICORN",
            "ZABBIX_PROXY_SERVICE",
            "NGROK_SERVICE",
            "PERSISTENT_DR_STATE_IDLE",
            "SLACK_DISTINCT_APPROVERS_GE_2",
            "INSUFFICIENT_APPROVERS",
            "CLOUDFLARE_ONPREM_CNAME_BASELINE",
            "TLS_CERT_KEY_MATCH",
            "AWS_THREE_DR_EC2_STOPPED_AND_ADDRESS_STABLE",
            "FIXED_NAT_AND_WEB_EIP_PRESERVED",
            "COMPLETE_BACKUP_MASTER_STATUS_PAIR",
            "DB_BACKUP_ENCRYPTION_KEY_MISSING",
            "DIRECT_NR_NOT_READY",
            "WAZUH_AGENT_NOT_READY",
            "SNMP_NOT_READY",
            "FAILOVER_FAILBACK_RUNBOOK_CONTRACT",
            "RTO_MEASUREMENT_READY",
            "RPO_MEASUREMENT_READY",
            "FINAL_DR_READY=",
        ):
            self.assertIn(marker, source)

    def test_checker_is_read_only_and_does_not_execute_workflows(self):
        source = self.read(SCRIPT)
        for forbidden in (
            "aws ec2 start-instances",
            "aws ec2 stop-instances",
            "create-nat-gateway",
            "delete-nat-gateway",
            "release-address",
            "terraform apply",
            "terraform destroy",
            "terraform state rm",
            "terraform state mv",
            "dr_dns_update_record",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("dr_state_read", source)
        self.assertNotRegex(source, re.compile(r"(?m)^\s*dr_state_write\s"))
        self.assertNotIn("dr_state_prepare", source)
        self.assertNotIn("dr_dns_apply_records ", source)

    def test_cloudflare_check_is_get_only_and_validates_full_baseline(self):
        source = self.read(SCRIPT)
        for marker in (
            "EXPECTED_RECORD_ID=\"4c35b40ec154fb21c92fc3795ce9d5ea\"",
            "EXPECTED_WEB_PUBLIC_IP=\"52.79.244.146\"",
            "EXPECTED_NAT_EIP=\"15.165.140.84\"",
            "zones/${CF_ZONE_ID}/dns_records/${CF_RECORD_ID}",
            '"type": "CNAME"',
            '"proxied": True',
            '"ttl": 1',
            "load_cloudflare_ssm_token",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("--request PUT", source)
        self.assertNotIn("--request POST", source)

    def test_terraform_drift_is_not_auto_reconciled(self):
        source = self.read(SCRIPT)
        for marker in (
            "OIDC_STATE_DRIFT_OPERATOR_RECONCILE",
            "KEY_PAIR_INPUT_REQUIRED",
            "MANAGEMENT_CIDR_INPUT_REQUIRED",
            "TERRAFORM_DESTRUCTIVE_PLAN_REVIEW_REQUIRED",
            "DR_TERRAFORM_PLAN_JSON",
            "terraform -chdir=\"$terraform_runtime_dir\" state list",
        ):
            self.assertIn(marker, source)

    def test_runbook_documents_the_read_only_gate(self):
        source = self.read(RUNBOOK)
        self.assertIn("final_dr_preflight.sh", source)
        self.assertIn("read-only", source)
        self.assertIn("FINAL_DR_READY", source)


if __name__ == "__main__":
    unittest.main()
