import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TERRAFORM = ROOT / "infra/terraform/main.tf"
STATUS = ROOT / "operations/aws/aws_dr_wireguard_status.sh"
PATH_PREFLIGHT = ROOT / "operations/aws/aws_dr_wireguard_path_preflight.sh"
PHASE3_RUNBOOK = ROOT / "docs/runbooks/FINAL_DR_INTEGRATION_AND_DEEP_FREEZE.md"
ANSIBLE_PLAYBOOK = ROOT / "automation/ansible/aws_dr_wireguard.yml"
ANSIBLE_PREPARE_PLAYBOOK = ROOT / "automation/ansible/aws_dr_wireguard_prepare.yml"
ANSIBLE_SITE = ROOT / "automation/ansible/site.yml"
WG_FILES = [
    TERRAFORM,
    STATUS,
    ROOT / "operations/aws/aws_dr_wireguard.env.example",
    ANSIBLE_PLAYBOOK,
    ANSIBLE_PREPARE_PLAYBOOK,
    *sorted((ROOT / "automation/ansible/roles/aws_dr_wireguard_gateway").rglob("*")),
    *sorted((ROOT / "automation/ansible/roles/aws_dr_wireguard_client").rglob("*")),
]


class AwsDrWireGuardContractTest(unittest.TestCase):
    def test_feature_is_disabled_by_default_and_uses_dedicated_range(self):
        source = TERRAFORM.read_text(encoding="utf-8")
        self.assertIn('variable "aws_dr_wg_enabled"', source)
        self.assertIn('default     = false', source[source.index('variable "aws_dr_wg_enabled"') :])
        self.assertIn('default     = "10.251.0.0/24"', source)
        self.assertIn('default     = "10.251.0.2/32"', source)
        self.assertIn('default     = 51821', source)
        self.assertIn('from_port   = 51820', source)
        self.assertIn('to_port     = 51820', source)
        self.assertIn('default     = ["1.209.148.228/32"]', source)
        self.assertIn('count                  = var.aws_dr_wg_enabled ? 1 : 0', source)

    def test_route_and_security_group_are_fail_closed(self):
        source = TERRAFORM.read_text(encoding="utf-8")
        route_start = source.index('resource "aws_route" "aws_dr_wg_onprem_db"')
        route_end = source.index('\nresource "aws_route" "private_internet"', route_start)
        route = source[route_start:route_end]
        for expected in (
            '"AWS_RESOURCE_ID_REDACTED"',
            '"AWS_RESOURCE_ID_REDACTED"',
            '"10.20.10.10"',
            '"AWS_RESOURCE_ID_REDACTED"',
            '"52.79.244.146"',
            '"AWS_RESOURCE_ID_REDACTED"',
            '"10.20.30.10"',
            'network_interface_id   = aws_instance.hospital_real_web.primary_network_interface_id',
        ):
            self.assertIn(expected, route)
        self.assertIn('aws_dr_wg_enabled ? [1] : []', source)
        self.assertIn('from_port   = var.aws_dr_wg_port', source)
        self.assertIn('cidr_blocks = var.aws_dr_wg_peer_ingress_ipv4_cidrs', source)
        self.assertIn('cidr_blocks = ["10.20.30.10/32"]', source)

    def test_web_gateway_source_destination_check_is_disabled(self):
        source = TERRAFORM.read_text(encoding="utf-8")
        start = source.index('resource "aws_instance" "hospital_real_web"')
        end = source.index('\nresource "aws_eip" "web_eip"', start)
        self.assertIn('source_dest_check           = false', source[start:end])

    def test_existing_msp_and_legacy_ranges_are_not_used_as_configuration(self):
        for path in WG_FILES:
            if not path.is_file():
                continue
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("10.250.0.0/24", source, str(path))
            self.assertNotIn("192.168.200.0/24", source, str(path))
        env_example = (ROOT / "operations/aws/aws_dr_wireguard.env.example").read_text()
        self.assertIn("AWS_DR_WG_INTERFACE=wg-aws-dr", env_example)
        self.assertNotIn("AWS_DR_WG_INTERFACE=wg0", env_example)

    def test_routed_design_does_not_add_masquerade(self):
        for path in WG_FILES:
            if path.is_file():
                self.assertNotIn("MASQUERADE", path.read_text(encoding="utf-8"), str(path))
        client_template = (
            ROOT
            / "automation/ansible/roles/aws_dr_wireguard_client/templates/wg-aws-dr.conf.j2"
        ).read_text(encoding="utf-8")
        self.assertIn("AllowedIPs = {{ aws_dr_wg_aws_db_ip }}", client_template)
        self.assertIn("PersistentKeepalive = 25", client_template)
        gateway_template = (
            ROOT
            / "automation/ansible/roles/aws_dr_wireguard_gateway/templates/wg-aws-dr.conf.j2"
        ).read_text(encoding="utf-8")
        self.assertIn("AllowedIPs = {{ aws_dr_wg_onprem_address }}", gateway_template)
        self.assertIn("--dport 3306", gateway_template)
        self.assertIn("--sport 3306", gateway_template)

    def test_keys_are_external_and_not_in_repository(self):
        for path in WG_FILES:
            if path.is_file():
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("BEGIN PRIVATE KEY", source, str(path))
                self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", source, str(path))
        gateway_tasks = (ROOT / "automation/ansible/roles/aws_dr_wireguard_gateway/tasks/main.yml").read_text()
        client_tasks = (ROOT / "automation/ansible/roles/aws_dr_wireguard_client/tasks/main.yml").read_text()
        self.assertIn("no_log: true", gateway_tasks)
        self.assertIn("no_log: true", client_tasks)
        self.assertIn("slurp", gateway_tasks)
        self.assertIn("slurp", client_tasks)

    def test_explicit_playbook_is_not_part_of_normal_provisioning(self):
        playbook = ANSIBLE_PLAYBOOK.read_text(encoding="utf-8")
        site = ANSIBLE_SITE.read_text(encoding="utf-8")
        self.assertIn("aws_dr_wg_gateway", playbook)
        self.assertIn("onprem_db_wg", playbook)
        self.assertNotIn("aws_dr_wireguard", site)

    def test_prepare_playbook_is_explicit_client_config_only(self):
        playbook = ANSIBLE_PREPARE_PLAYBOOK.read_text(encoding="utf-8")
        site = ANSIBLE_SITE.read_text(encoding="utf-8")
        self.assertIn("hosts: onprem_db_wg", playbook)
        self.assertIn("aws_dr_wg_enabled: true", playbook)
        self.assertIn("aws_dr_wg_prepare_only: true", playbook)
        self.assertNotIn("aws_dr_wg_gateway", playbook)
        self.assertNotIn("state: started", playbook)
        self.assertNotIn("aws_dr_wireguard_prepare", site)

    def test_prepare_mode_preserves_live_fail_closed_guard_and_skips_start(self):
        client = (
            ROOT
            / "automation/ansible/roles/aws_dr_wireguard_client/tasks/main.yml"
        ).read_text(encoding="utf-8")
        normalized = " ".join(client.split())
        self.assertIn("aws_dr_wg_prepare_only | bool", client)
        self.assertIn("aws_dr_wg_onprem_private_key_stat.stat.mode == '0600'", client)
        self.assertIn("dest: \"/etc/wireguard/{{ aws_dr_wg_interface }}.conf\"", client)
        self.assertIn("mode: '0600'", client)
        self.assertIn("will never stop the legacy tunnel automatically", normalized)
        self.assertIn("Preparation-only mode", client)

        # The legacy interface/route assertion is a live-cutover guard only.
        # Preparation must be able to observe an active rollback path without
        # stopping it or deleting its route.
        self.assertGreaterEqual(
            normalized.count("not (aws_dr_wg_prepare_only | bool)"),
            2,
        )
        self.assertIn("aws_dr_wg_legacy_interface.rc != 0", client)
        self.assertIn(
            "(aws_dr_wg_legacy_route.stdout | default('') | trim) == ''",
            client,
        )

    def test_prepare_only_has_no_runtime_mutation_tasks(self):
        playbook = ANSIBLE_PREPARE_PLAYBOOK.read_text(encoding="utf-8")
        self.assertNotIn("state: started", playbook)
        self.assertNotIn("state: stopped", playbook)
        self.assertNotIn("ip route add", playbook)
        self.assertNotIn("ip route del", playbook)
        self.assertNotIn("wg-quick", playbook)

    def test_prepare_only_legacy_active_is_allowed_but_live_cutover_is_not(self):
        client = (
            ROOT
            / "automation/ansible/roles/aws_dr_wireguard_client/tasks/main.yml"
        ).read_text(encoding="utf-8")
        guard_start = client.index("- name: Refuse the new client while the legacy route is active")
        guard_end = client.index("- name: Record the preserved legacy path", guard_start)
        guard = client[guard_start:guard_end]
        self.assertIn("aws_dr_wg_legacy_interface.rc != 0", guard)
        self.assertIn(
            "(aws_dr_wg_legacy_route.stdout | default('') | trim) == ''",
            guard,
        )
        self.assertIn("not (aws_dr_wg_prepare_only | bool)", guard)
        self.assertIn("aws_dr_wg_enabled | bool", guard)

    def test_all_live_ansible_tasks_are_feature_gated(self):
        for path in (
            ROOT / "automation/ansible/roles/aws_dr_wireguard_gateway/tasks/main.yml",
            ROOT / "automation/ansible/roles/aws_dr_wireguard_client/tasks/main.yml",
        ):
            source = path.read_text(encoding="utf-8")
            self.assertGreaterEqual(source.count("when: aws_dr_wg_enabled | bool"), 8, str(path))

    def test_status_is_read_only_and_disabled_by_default(self):
        source = STATUS.read_text(encoding="utf-8")
        self.assertNotIn("terraform apply", source)
        self.assertNotIn("terraform destroy", source)
        self.assertNotIn("ip link add", source)
        self.assertNotIn("wg-quick", source)
        result = subprocess.run(
            ["/bin/bash", str(STATUS)],
            cwd=ROOT,
            env={"AWS_DR_WG_ENABLED": "false", "PATH": ""},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("AWS_DR_WG_STATUS=DISABLED_BY_DESIGN", result.stdout)
        self.assertIn("CONFIG_AVAILABLE=YES", result.stdout)

    def test_path_preflight_is_read_only_and_fail_closed(self):
        source = PATH_PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn("READ_ONLY_DIAGNOSTIC=YES", source)
        self.assertIn("AWS_DR_WG_CUTOVER=BLOCKED_UNTIL_ONPREM_PATH_VERIFIED", source)
        self.assertIn('EXPECTED_DB_HOST="192.168.200.4"', source)
        self.assertIn('EXPECTED_GATEWAY="10.10.10.1"', source)
        self.assertNotIn("wg-quick up", source)
        self.assertNotIn("wg-quick down", source)
        self.assertNotIn("ip route add", source)

    def test_client_refuses_active_legacy_route(self):
        source = (
            ROOT
            / "automation/ansible/roles/aws_dr_wireguard_client/tasks/main.yml"
        ).read_text(encoding="utf-8")
        normalized = " ".join(source.split())
        self.assertIn("ip link show wg-aws-hub", source)
        self.assertIn("ip route show 10.20.30.10/32", source)
        self.assertIn("(aws_dr_wg_legacy_route.stdout | default('') | trim) == ''", source)
        self.assertIn("Refuse the new client while the legacy route is active", source)
        self.assertIn("will never stop the legacy tunnel automatically", normalized)

    def test_status_rejects_unknown_role(self):
        result = subprocess.run(
            ["/bin/bash", str(STATUS)],
            cwd=ROOT,
            env={"AWS_DR_WG_ENABLED": "false", "AWS_DR_WG_ROLE": "msp"},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("AWS_DR_WG_CONFIG_ERROR", result.stderr)

    def test_status_rejects_wrong_interface_before_live_commands(self):
        result = subprocess.run(
            ["bash", str(STATUS)],
            cwd=ROOT,
            env={
                "AWS_DR_WG_ENABLED": "true",
                "AWS_DR_WG_INTERFACE": "wg0",
            },
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("AWS_DR_WG_CONFIG_ERROR", result.stderr)

    def test_runtime_contract_documents_preserved_source_ip(self):
        docs = (ROOT / "docs/runbooks/AWS_DR_MONITORING.md").read_text(encoding="utf-8")
        self.assertIn("10.251.0.1/24", docs)
        self.assertIn("10.251.0.2/32", docs)
        self.assertIn("10.20.30.10", docs)
        self.assertIn("No MASQUERADE", docs)
        self.assertIn("AWS_DR_WG_ENABLED=false", docs)

    def test_phase3_runbook_freezes_cutover_and_nat_boundaries(self):
        docs = PHASE3_RUNBOOK.read_text(encoding="utf-8")
        for expected in (
            "FIXED_SLACK_ENDPOINT=PASS",
            "WG_PRE_CUTOVER_READY=YES",
            "NR_PRECHECK_READY=YES",
            "EXPECTED_WHILE_WEB_STOPPED",
            "private_egress_mode=web_nat_instance",
            "enable_nat_gateway=false",
            "operations/nat/nat_enable.sh",
            "MUST_NOT_USE",
            "fresh dump/position",
            "RESET REPLICA",
            "DB_NR_ENABLED=false",
            "wg-quick@wg-aws-hub.service",
        ):
            self.assertIn(expected, docs)


if __name__ == "__main__":
    unittest.main()
