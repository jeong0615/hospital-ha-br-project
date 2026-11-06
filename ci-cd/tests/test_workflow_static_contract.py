import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class WorkflowStaticContractTest(unittest.TestCase):
    def read(self, relative_path):
        return (ROOT / relative_path).read_text()

    def test_chatops_failback_uses_complete_entrypoint(self):
        source = self.read("operations/chatops/chatops_core.py")
        self.assertIn('os.path.join(script_root, "web_db_failback.sh")', source)
        self.assertNotIn('os.path.join(script_root, "full_failback.sh")', source)

    def test_wrapper_preserves_db_doc_order_before_dns(self):
        source = self.read("operations/failover/full_failback.sh")
        db_call = source.index('"${DB_DIR}/db_failback_to_onprem.sh"')
        doc_call = source.index('"${DB_DIR}/doc_failback_sync.sh"')
        dns_call = source.index('"${FAILOVER_DIR}/failback.sh"')
        self.assertLess(db_call, doc_call)
        self.assertLess(doc_call, dns_call)
        self.assertIn('RUN_DB_FAILBACK:-false', source)
        self.assertIn('RUN_DOC_FAILBACK:-false', source)

    def test_repo_contains_chatops_launchers_and_run_demo(self):
        self.assertTrue((ROOT / "infra/terraform/run_demo.sh").is_file())
        self.assertTrue((ROOT / "infra/terraform/full_failover.sh").is_file())
        self.assertTrue((ROOT / "infra/terraform/web_db_failback.sh").is_file())

    def test_cloudflare_status_uses_runtime_zone_configuration(self):
        source = self.read("operations/finops/dr_full_status.sh")
        self.assertIn('CF_ZONE_ID="${CF_ZONE_ID:-}"', source)
        self.assertIn('require_config "CF_ZONE_ID"', source)
        self.assertIn('zones/${CF_ZONE_ID}/dns_records', source)
        self.assertNotIn('zones/1b91ec95d7d8131b5ce5c8ed0317c451/dns_records', source)
        self.assertIn("CF_ZONE_ID=", self.read("operations/chatops/.env.example"))

    def test_failback_wrapper_has_fail_safe_lock_and_shell_mode(self):
        source = self.read("operations/failover/full_failback.sh")
        self.assertIn("set -euo pipefail", source)
        self.assertIn('flock', self.read("operations/failover/failback.sh"))
        self.assertIn("pipeline_lock", self.read("operations/chatops/chatops_core.py"))

    def test_live_workflows_do_not_mutate_retired_nat_gateway(self):
        failover = self.read("operations/failover/full_failover.sh")
        db_failback = self.read("operations/db/db_failback_to_onprem.sh")
        full_failback = self.read("operations/failover/full_failback.sh")
        self.assertNotIn("nat_enable.sh", failover)
        self.assertNotIn("nat_enable.sh", db_failback)
        self.assertNotIn("nat_disable.sh", full_failback)
        self.assertIn("web_nat_instance_status.sh", failover)
        self.assertIn("web_nat_instance_status.sh", db_failback)

    def test_timing_boundaries_exist_for_ordered_stages(self):
        sources = {
            "failover": self.read("operations/failover/full_failover.sh"),
            "failback": self.read("operations/failover/full_failback.sh"),
            "db": self.read("operations/db/db_failback_to_onprem.sh"),
            "doc": self.read("operations/db/doc_failback_sync.sh"),
        }
        for source in sources.values():
            self.assertIn("dr_timing_start", source)
            self.assertIn("dr_timing_end", source)


    def test_quality_gate_covers_required_checks_and_gates_ecr(self):
        source = self.read(".github/workflows/docker-security-scan.yml")
        required = (
            "python -m unittest discover",
            "bash -n",
            "ansible-playbook",
            "shellcheck --severity=error",
            "terraform fmt -check -recursive",
            "terraform init -backend=false",
            "terraform validate -no-color",
            "gitleaks detect",
            "docker/build-push-action@v7",
            "push: false",
            "operations/security/build_security_summary.py",
            "security-summary.json",
            "actions/upload-artifact@v4",
            "actions/download-artifact@v4",
            "DEVSECOPS_SUMMARY_JSON_BEGIN",
            "Emit structured summary to run log",
        )
        for marker in required:
            self.assertIn(marker, source)
        self.assertIn("hashicorp/setup-terraform@v4", source)
        self.assertNotIn("hashicorp/setup-terraform@v3", source)

        quality_block = source.split("\n  quality_gate:\n", 1)[1].split(
            "\n  build-scan-test:\n", 1
        )[0]
        for dependency in (
            "python_tests",
            "bash_validation",
            "ansible_validation",
            "terraform_validation",
            "secret_scan",
            "static_contract",
            "build-scan-test",
            "emr-mysql2-compatibility",
        ):
            self.assertIn(f"      - {dependency}", quality_block)

        push_block = source.split("\n  push-ecr-images:\n", 1)[1]
        self.assertRegex(
            push_block,
            r"needs:\n(?:      - [^\n]+\n)*      - quality_gate\n",
        )
        self.assertIn("needs.quality_gate.result == 'success'", push_block)
        self.assertIn("github.event_name == 'push'", push_block)
        self.assertIn("github.ref == 'refs/heads/main'", push_block)
        self.assertIn("ECR_STAGING_TAG", source)
        self.assertIn("Stage security-gated image in ECR", source)
        self.assertIn("Delete temporary ECR staging image", source)
        self.assertIn("Validate GitHub OIDC subject contract", source)
        self.assertIn("Artifact storage is a GitHub service quota", source)
        self.assertIn("hashFiles(format('reports/{0}/{0}-security-summary.json', matrix.name)) != ''", source)
        self.assertGreaterEqual(source.count("continue-on-error: true"), 3)
        self.assertNotIn("actions/upload-artifact@v7", source)
        self.assertNotIn("actions/download-artifact@v8", source)

    def test_quality_workflow_trigger_and_permissions_are_minimal(self):
        source = self.read(".github/workflows/docker-security-scan.yml")
        header = source.split("\njobs:\n", 1)[0]
        self.assertIn("pull_request:", header)
        self.assertRegex(header, r"push:\n\s+branches: \[main\]")
        self.assertIn("permissions:\n  contents: read", header)
        self.assertNotIn("id-token: write", header)

    def test_ephemeral_ci_secret_is_masked_before_export(self):
        source = self.read(".github/workflows/docker-security-scan.yml")
        self.assertIn('ci_session_secret="$(openssl rand -hex 32)"', source)
        self.assertIn('echo "::add-mask::${ci_session_secret}"', source)
        self.assertIn("printf 'CI_SESSION_SECRET=%s\\n'", source)
        self.assertNotIn(
            'echo "CI_SESSION_SECRET=$(openssl rand -hex 32)" >> "${GITHUB_ENV}"',
            source,
        )

    def test_retired_aws_wireguard_and_s3_head_contracts_are_absent(self):
        forbidden_wireguard = "10.250.0." + "0/24"
        forbidden_s3_head = "head-" + "object"
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or "tests" in path.parts:
                continue
            if path.suffix in {".lock", ".pyc"}:
                continue
            try:
                source = path.read_text()
            except UnicodeDecodeError:
                continue
            self.assertNotIn(forbidden_wireguard, source, str(path))
            self.assertNotIn(forbidden_s3_head, source, str(path))


if __name__ == "__main__":
    unittest.main()
