import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TERRAFORM = ROOT / "infra/terraform/main.tf"
ROLE = ROOT / "automation/ansible/roles/aws_web_nat_instance"
TASKS = ROLE / "tasks/main.yml"
HANDLERS = ROLE / "handlers/main.yml"
PLAYBOOK = ROOT / "automation/ansible/aws_web_nat_instance.yml"
DISABLE_PLAYBOOK = ROOT / "automation/ansible/aws_web_nat_instance_disable.yml"
SITE = ROOT / "automation/ansible/site.yml"
STATUS = ROOT / "operations/nat_instance/web_nat_instance_status.sh"
PLAN_GUARD = ROOT / "operations/nat_instance/web_nat_instance_plan_guard.sh"
DOC = ROOT / "docs/runbooks/NAT_INSTANCE_MIGRATION.md"


class WebNatInstanceContractTest(unittest.TestCase):
    def test_terraform_is_opt_in_and_keeps_nat_gateway_default(self):
        source = TERRAFORM.read_text(encoding="utf-8")
        self.assertIn('variable "private_egress_mode"', source)
        self.assertIn('default     = "nat_gateway"', source)
        self.assertIn('variable "aws_web_nat_instance_enabled"', source)
        self.assertIn('default     = false', source)
        self.assertIn('var.private_egress_mode == "web_nat_instance"', source)
        self.assertIn('network_interface_id   = var.private_egress_mode == "web_nat_instance"', source)
        self.assertIn('nat_gateway_id         = var.private_egress_mode == "nat_gateway"', source)
        self.assertIn("BLACKHOLE recovery contract", source)
        self.assertIn('enable_nat_gateway=true', DOC.read_text(encoding="utf-8"))

    def test_route_and_web_identity_are_fail_closed(self):
        source = TERRAFORM.read_text(encoding="utf-8")
        for expected in (
            '"AWS_RESOURCE_ID_REDACTED"',
            '"AWS_RESOURCE_ID_REDACTED"',
            '"AWS_RESOURCE_ID_REDACTED"',
            '"10.20.10.10"',
            '"52.79.244.146"',
            'aws_web_nat_instance_enabled',
        ):
            self.assertIn(expected, source)
        self.assertIn('source_dest_check           = false', source)

    def test_wireguard_and_private_destinations_are_excluded_from_nat(self):
        role_defaults = (ROLE / "defaults/main.yml").read_text(encoding="utf-8")
        script = (ROLE / "templates/hospital-dr-web-nat-instance.sh.j2").read_text(
            encoding="utf-8"
        )
        for expected in ("10.20.0.0/16", "10.251.0.0/24", "192.168.200.0/24"):
            self.assertIn(expected, role_defaults)
        self.assertIn("aws_web_nat_instance_forward_chain", script)
        self.assertIn("aws_web_nat_instance_postrouting_chain", script)
        self.assertIn("HOSPITAL-DR-NAT-FWD", role_defaults)
        self.assertIn("HOSPITAL-DR-NAT-POSTROUTING", role_defaults)
        self.assertNotIn("iptables -F", script)
        self.assertNotIn("iptables -t nat -F", script)
        self.assertNotIn("wg0", role_defaults)
        self.assertNotIn("10.250.", role_defaults)

    def test_exclusions_use_early_return_chains_not_multiple_destination_flags(self):
        script = (ROLE / "templates/hospital-dr-web-nat-instance.sh.j2").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'iptables_cmd -A "$FORWARD_CHAIN" -d "$excluded" -j RETURN', script
        )
        self.assertIn(
            'iptables_nat_cmd -A "$POSTROUTING_CHAIN" -d "$excluded" -j RETURN',
            script,
        )
        self.assertIn(
            'iptables_cmd -A "$FORWARD_CHAIN" -s "$cidr" -o "$UPLINK" -j ACCEPT',
            script,
        )
        self.assertIn(
            'iptables_nat_cmd -A "$POSTROUTING_CHAIN" -s "$cidr" -o "$UPLINK" -j MASQUERADE',
            script,
        )
        self.assertNotIn('rule+=( ! -d "$excluded" )', script)
        self.assertNotIn('nat_rule+=( ! -d "$excluded" )', script)

    def test_explicit_playbooks_are_not_normal_site(self):
        playbook = PLAYBOOK.read_text(encoding="utf-8")
        disable = DISABLE_PLAYBOOK.read_text(encoding="utf-8")
        site = SITE.read_text(encoding="utf-8")
        self.assertIn("aws_web_nat_instance", playbook)
        self.assertIn("aws_web_nat_instance_action: remove", disable)
        self.assertNotIn("aws_web_nat_instance", site)

    def test_check_mode_skips_runtime_mutations_but_keeps_diff_tasks(self):
        tasks = TASKS.read_text(encoding="utf-8")
        handlers = HANDLERS.read_text(encoding="utf-8")

        self.assertIn('name: Apply only the project-owned NAT chains', tasks)
        self.assertIn('name: Enable the NAT Instance service without touching WireGuard services', tasks)
        self.assertIn('name: Stop the project-owned NAT service', tasks)
        self.assertIn('when: not ansible_check_mode', tasks)
        self.assertIn('when: not ansible_check_mode', handlers)

        apply_block = tasks.split('name: Configure the Web NAT Instance', 1)[1]
        self.assertIn('ansible.builtin.copy:', apply_block)
        self.assertIn('ansible.builtin.template:', apply_block)
        self.assertIn('ansible.builtin.command: "{{ aws_web_nat_instance_script }} apply"', apply_block)
        self.assertIn('when: not ansible_check_mode', apply_block)
        self.assertIn('name: Ensure iptables tooling exists', apply_block)
        self.assertIn('name: Verify iptables is available without changing firewall state', tasks)
        self.assertNotIn('check_mode: false', tasks)

        # Check mode must never execute the project-owned firewall controller or
        # systemd activation, while copy/template tasks remain check-mode aware.
        self.assertNotIn('check_mode: false\n      when: not ansible_check_mode', apply_block)

    def test_status_and_plan_guard_are_read_only_guards(self):
        status = STATUS.read_text(encoding="utf-8")
        guard = PLAN_GUARD.read_text(encoding="utf-8")
        for source in (status, guard):
            self.assertNotIn("terraform apply", source)
            self.assertNotIn("terraform destroy", source)
        self.assertIn("aws_route.private_internet", guard)
        self.assertIn("NAT_GATEWAY_CREATE_PLANNED=NO", guard)
        self.assertIn("NAT_GATEWAY_RECREATE_PLANNED=NO", guard)
        self.assertIn("--allow-none-egress", guard)
        self.assertIn("READY_FOR_BLACKHOLE_RECOVERY", status)
        self.assertIn("NAT_EIP_STATE=ALLOCATED_UNASSOCIATED", status)
        for protected in (
            "aws_nat_gateway.nat_gw",
            "aws_eip.nat_eip",
            "aws_instance.hospital_real_web",
            "aws_instance.doc_server",
            "aws_instance.db_server",
        ):
            self.assertIn(protected, guard)

    def test_status_script_shell_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(STATUS)], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_migration_document_has_route_only_guard_and_rollback(self):
        source = DOC.read_text(encoding="utf-8")
        for expected in (
            "NAT_INSTANCE_PLAN=PASS",
            "BLACKHOLE_RECOVERY",
            "enable_nat_gateway=false",
            "NAT_GATEWAY_CREATE_PLANNED=NO",
            "NAT_GATEWAY_DESTROY=NO",
            "--allow-none-egress",
            "private_egress_mode=none",
            "private_egress_mode=web_nat_instance",
        ):
            self.assertIn(expected, source)

    def test_plan_guard_accepts_route_only_and_rejects_protected_changes(self):
        def run_guard(resource_changes):
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                plan = base / "migration.tfplan"
                plan.write_text("fixture", encoding="utf-8")
                fake_terraform = base / "terraform"
                fake_terraform.write_text(
                    "#!/usr/bin/env python3\n"
                    "import json\n"
                    "import sys\n"
                    f"print(json.dumps({{'resource_changes': {resource_changes!r}}}))\n",
                    encoding="utf-8",
                )
                fake_terraform.chmod(0o750)
                env = os.environ.copy()
                env["PATH"] = f"{base}:{env['PATH']}"
                return subprocess.run(
                    ["bash", str(PLAN_GUARD), str(plan)],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )

        route_only = [
            {
                "address": "aws_route.private_internet[0]",
                "change": {
                    "actions": ["update"],
                    "before": {
                        "route_table_id": "AWS_RESOURCE_ID_REDACTED",
                        "destination_cidr_block": "0.0.0.0/0",
                        "nat_gateway_id": "AWS_RESOURCE_ID_REDACTED",
                    },
                    "after": {
                        "route_table_id": "AWS_RESOURCE_ID_REDACTED",
                        "destination_cidr_block": "0.0.0.0/0",
                        "network_interface_id": "AWS_RESOURCE_ID_REDACTED",
                        "nat_gateway_id": None,
                    },
                },
            }
        ]
        accepted = run_guard(route_only)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("NAT_INSTANCE_PLAN=PASS", accepted.stdout)
        self.assertIn("NAT_GATEWAY_CREATE_PLANNED=NO", accepted.stdout)
        self.assertIn("ROUTE_MODE=WEB_NAT_INSTANCE", accepted.stdout)

        protected_ingress = [
            {
                "description": "AWS DR WireGuard listener",
                "from_port": 51820,
                "to_port": 51820,
                "protocol": "udp",
                "cidr_blocks": ["1.209.148.228/32"],
            },
            {
                "description": "AWS DR dedicated WireGuard listener",
                "from_port": 51821,
                "to_port": 51821,
                "protocol": "udp",
                "cidr_blocks": ["1.209.148.228/32"],
            },
            {
                "description": "AWS DR DB forwarded traffic",
                "from_port": 3306,
                "to_port": 3306,
                "protocol": "tcp",
                "cidr_blocks": ["10.20.30.10/32"],
            },
        ]
        nat_ingress = {
            "description": "Private-subnet traffic forwarded by the opt-in Web NAT Instance",
            "from_port": 0,
            "to_port": 0,
            "protocol": "-1",
            "cidr_blocks": ["10.20.10.0/24", "10.20.20.0/24", "10.20.30.0/24"],
            "ipv6_cidr_blocks": [],
            "prefix_list_ids": [],
            "security_groups": [],
            "self": False,
        }
        sg_before = {
            "id": "AWS_RESOURCE_ID_REDACTED",
            "name": "hospital-dr-web-sg",
            "vpc_id": "AWS_RESOURCE_ID_REDACTED",
            "ingress": protected_ingress,
            "egress": [{"protocol": "-1", "cidr_blocks": ["0.0.0.0/0"]}],
        }
        sg_after = dict(sg_before)
        sg_after["ingress"] = protected_ingress + [nat_ingress]
        route_and_sg = [
            route_only[0],
            {
                "address": "aws_security_group.web_sg",
                "change": {"actions": ["update"], "before": sg_before, "after": sg_after},
            },
        ]
        accepted_with_sg = run_guard(route_and_sg)
        self.assertEqual(accepted_with_sg.returncode, 0, accepted_with_sg.stderr)
        self.assertIn("WEB_SG_MODE=APPROVED_NAT_INGRESS_ONLY", accepted_with_sg.stdout)

        for protected_index in (0, 1):
            removed = list(protected_ingress)
            removed.pop(protected_index)
            bad_after = dict(sg_before)
            bad_after["ingress"] = removed + [nat_ingress]
            rejected_protected = run_guard([
                route_only[0],
                {
                    "address": "aws_security_group.web_sg",
                    "change": {"actions": ["update"], "before": sg_before, "after": bad_after},
                },
            ])
            self.assertNotEqual(rejected_protected.returncode, 0)
            self.assertIn("existing Web security-group ingress rule", rejected_protected.stderr)

        bad_public_ingress = dict(sg_before)
        bad_public_ingress["ingress"] = protected_ingress + [
            dict(nat_ingress, cidr_blocks=["0.0.0.0/0"])
        ]
        rejected_unrelated_sg = run_guard([
            route_only[0],
            {
                "address": "aws_security_group.web_sg",
                "change": {"actions": ["update"], "before": sg_before, "after": bad_public_ingress},
            },
        ])
        self.assertNotEqual(rejected_unrelated_sg.returncode, 0)
        self.assertIn("approved NAT ingress addition", rejected_unrelated_sg.stderr)

        for address, action in (
            ("aws_nat_gateway.nat_gw[0]", "create"),
            ("aws_nat_gateway.nat_gw[0]", "delete"),
            ("aws_eip.nat_eip[0]", "update"),
            ("aws_instance.hospital_real_web", "replace"),
            ("aws_route.aws_dr_wg_db", "update"),
            ("aws_route.unrelated", "update"),
        ):
            rejected = run_guard([
                {"address": address, "change": {"actions": [action]}},
                route_only[0],
            ])
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("unexpected resource changes", rejected.stderr)

        no_egress = [
            {
                "address": "aws_route.private_internet[0]",
                "change": {
                    "actions": ["delete"],
                    "before": {
                        "route_table_id": "AWS_RESOURCE_ID_REDACTED",
                        "destination_cidr_block": "0.0.0.0/0",
                        "network_interface_id": "AWS_RESOURCE_ID_REDACTED",
                    },
                    "after": None,
                },
            }
        ]
        rejected_no_egress = run_guard(no_egress)
        self.assertNotEqual(rejected_no_egress.returncode, 0)
        accepted_no_egress = run_guard(no_egress + [])
        # The default guard must reject no-egress changes; the explicit option
        # is tested by invoking the helper with the optional argument below.
        self.assertNotEqual(accepted_no_egress.returncode, 0)

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            plan = base / "no-egress.tfplan"
            plan.write_text("fixture", encoding="utf-8")
            fake_terraform = base / "terraform"
            fake_terraform.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "import sys\n"
                f"print(json.dumps({{'resource_changes': {no_egress!r}}}))\n",
                encoding="utf-8",
            )
            fake_terraform.chmod(0o750)
            env = os.environ.copy()
            env["PATH"] = f"{base}:{env['PATH']}"
            accepted_no_egress = subprocess.run(
                ["bash", str(PLAN_GUARD), str(plan), "--allow-none-egress"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(accepted_no_egress.returncode, 0, accepted_no_egress.stderr)
        self.assertIn("ROUTE_MODE=NONE_EGRESS_ROLLBACK", accepted_no_egress.stdout)

    def test_status_classifies_blackhole_recovery_without_mutating_aws(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            fake_aws = base / "aws"
            fake_aws.write_text(
                "#!/usr/bin/env bash\n"
                "case \"$*\" in\n"
                "  *describe-instances*) printf 'running\\t10.20.10.10\\t52.79.244.146\\tFalse\\tsubnet-09199b5bc3152ca2a\\teni-05920ba21682a3852\\n' ;;\n"
                "  *describe-route-tables*) printf 'blackhole\\tnat-0ac4052ad03e3b2ff\\tNone\\n' ;;\n"
                "  *describe-addresses*) printf 'AWS_RESOURCE_ID_REDACTED\\t15.165.140.84\\tNone\\n' ;;\n"
                "  *describe-nat-gateways*) : ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake_aws.chmod(0o750)
            env = os.environ.copy()
            env["PATH"] = f"{base}:{env['PATH']}"
            result = subprocess.run(
                ["bash", str(STATUS)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CURRENT_EGRESS_MODE=BLACKHOLE", result.stdout)
        self.assertIn("NAT_INSTANCE_STATUS=READY_FOR_BLACKHOLE_RECOVERY", result.stdout)
        self.assertIn("NAT_EIP_STATE=ALLOCATED_UNASSOCIATED", result.stdout)


if __name__ == "__main__":
    unittest.main()
