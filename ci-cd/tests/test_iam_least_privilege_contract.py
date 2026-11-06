import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
TERRAFORM_CANDIDATES = ROOT / "infra/terraform/iam_least_privilege_candidates.tf"
DRIFT_CHECK = ROOT / "operations/runtime/terraform_drift_check.sh"


class IamLeastPrivilegeContractTest(unittest.TestCase):
    def test_candidates_are_explicit_and_not_attached(self):
        source = TERRAFORM_CANDIDATES.read_text(encoding="utf-8")
        self.assertIn(
            'data "aws_iam_policy_document" "terraform_admin_least_privilege_candidate"',
            source,
        )
        self.assertIn(
            'data "aws_iam_policy_document" "terraform_admin_break_glass_candidate"',
            source,
        )
        self.assertIn(
            'data "aws_iam_policy_document" "dr_cost_guard_least_privilege_candidate"',
            source,
        )
        self.assertNotIn(
            "policy = data.aws_iam_policy_document.terraform_admin_least_privilege_candidate",
            source,
        )
        self.assertNotIn(
            "policy = data.aws_iam_policy_document.dr_cost_guard_least_privilege_candidate",
            source,
        )

        for wildcard_action in ("iam:*", "ec2:*", "s3:*", "ssm:*"):
            self.assertNotIn(f'"{wildcard_action}"', source)
        self.assertNotIn('actions = ["*"]', source)

        daily_candidate = source.split(
            'data "aws_iam_policy_document" "terraform_admin_break_glass_candidate"',
            1,
        )[0]
        for destructive_action in (
            "ec2:TerminateInstances",
            "ec2:DeleteVpc",
            "ec2:ReleaseAddress",
            "s3:DeleteBucket",
        ):
            self.assertNotIn(f'"{destructive_action}"', daily_candidate)

    def test_cost_guard_candidate_has_no_nat_mutation_permissions(self):
        source = TERRAFORM_CANDIDATES.read_text(encoding="utf-8")
        candidate = source.split(
            'data "aws_iam_policy_document" "dr_cost_guard_least_privilege_candidate"',
            1,
        )[1]
        self.assertIn('variable = "ec2:ResourceTag/Name"', candidate)
        self.assertIn(":instance/*", candidate)
        self.assertIn("ec2:StopInstances", candidate)
        self.assertNotIn("ec2:DeleteNatGateway", candidate)
        self.assertNotIn("ec2:ReleaseAddress", candidate)
        self.assertNotIn(":natgateway/*", candidate)
        self.assertNotIn(":elastic-ip/*", candidate)
        self.assertNotIn('"ec2:*"', candidate)

    def test_oidc_and_ecr_contract_remain_narrow(self):
        oidc = (ROOT / "infra/terraform/github_oidc.tf").read_text(encoding="utf-8")
        self.assertIn(
            'default = "jeong0615@299385053/hospital-ci-cd@1354475545"',
            oidc,
        )
        self.assertIn('default     = "main"', oidc)
        self.assertIn('"sts.amazonaws.com"', oidc)
        self.assertIn('sid     = "GitHubActionsMainSubject"', oidc)
        self.assertNotIn("github_oidc_immutable_subject", oidc)
        self.assertNotIn("StringLike", oidc)
        self.assertIn("aws_ecr_repository.dr_web.arn", oidc)
        self.assertIn("aws_ecr_repository.dr_emr.arn", oidc)
        for action in (
            "ecr:BatchGetImage",
            "ecr:GetDownloadUrlForLayer",
            "ecr:BatchDeleteImage",
        ):
            self.assertIn(action, oidc)
        self.assertNotIn('actions = ["*"]', oidc)

        terraform_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "infra/terraform").glob("*.tf")
            if path.name != "iam_least_privilege_candidates.tf"
        )
        self.assertNotIn("AdministratorAccess", terraform_sources)

    def test_drift_checker_reports_known_operator_owned_changes(self):
        plan = {
            "resource_changes": [
                {
                    "address": "aws_iam_role.github_actions_ecr_push",
                    "change": {"actions": ["update"], "before": {}, "after": {}},
                },
                {
                    "address": "aws_key_pair.dr_key",
                    "change": {"actions": ["create", "delete"], "before": {}, "after": {}},
                },
                {
                    "address": "aws_security_group.web_sg",
                    "change": {
                        "actions": ["update"],
                        "before": {"ingress": [{"cidr_blocks": ["1.2.3.4/32"]}]},
                        "after": {"ingress": [{"cidr_blocks": ["5.6.7.8/32"]}]},
                    },
                },
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            result = subprocess.run(
                [str(DRIFT_CHECK), "--plan-json", str(plan_path)],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OIDC_STATE_DRIFT=true", result.stdout)
        self.assertIn("KEY_PAIR_MISMATCH=true", result.stdout)
        self.assertIn("MANAGEMENT_CIDR_NEEDS_INPUT=true", result.stdout)
        self.assertIn("TERRAFORM_DRIFT_CHECK_READY=false", result.stdout)

    def test_drift_checker_is_read_only_for_noop_plan(self):
        plan = {"resource_changes": [{"address": "aws_vpc.dr_vpc", "change": {"actions": ["no-op"]}}]}
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            before = plan_path.read_bytes()
            result = subprocess.run(
                [str(DRIFT_CHECK), "--plan-json", str(plan_path)],
                text=True,
                capture_output=True,
                check=False,
            )
            after = plan_path.read_bytes()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("TERRAFORM_DRIFT_CHECK_READY=true", result.stdout)
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
