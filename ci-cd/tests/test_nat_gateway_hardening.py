import json
import os
import pwd
import grp
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
NAT_DIR = ROOT / "operations/nat"
EXPECTED = {
    "vpc_id": "AWS_RESOURCE_ID_REDACTED",
    "allocation_id": "AWS_RESOURCE_ID_REDACTED",
    "public_ip": "15.165.140.84",
    "subnet_id": "AWS_RESOURCE_ID_REDACTED",
    "route_table_id": "AWS_RESOURCE_ID_REDACTED",
}


class NatGatewayLifecycleTest(unittest.TestCase):
    def run_nat(
        self,
        action,
        *,
        state_overrides=None,
        initial_nat=False,
        initial_route=False,
        post_nat_present=None,
        apply_rc=0,
        nat_details=None,
        route_details=None,
        terraform_dir_override=None,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            runtime_tf = base / "terraform"
            runtime_tf.mkdir()
            (runtime_tf / "terraform.tfstate").write_text("fixture\n", encoding="utf-8")

            operations = base / "operations"
            runtime = operations / "runtime"
            nat_dir = operations / "nat"
            runtime.mkdir(parents=True)
            nat_dir.mkdir(parents=True)
            shutil.copy(ROOT / "operations/runtime_paths.sh", operations / "runtime_paths.sh")
            shutil.copy(ROOT / "operations/runtime/dr_state.sh", runtime / "dr_state.sh")
            for source in NAT_DIR.iterdir():
                shutil.copy(source, nat_dir / source.name)

            # The immutable production path is replaced only inside the test
            # copy; the real scripts cannot be redirected by environment input.
            common = nat_dir / "nat_common.sh"
            common.write_text(
                common.read_text(encoding="utf-8").replace(
                    'EXPECTED_TERRAFORM_DIR="/home/cloud/hospital-dr-terraform"',
                    f'EXPECTED_TERRAFORM_DIR="{runtime_tf}"',
                ),
                encoding="utf-8",
            )

            base_state = [
                "aws_vpc.dr_vpc",
                "aws_eip.nat_eip",
                "aws_subnet.public_subnet",
                "aws_route_table.private_rt",
            ]
            if initial_nat:
                base_state.append("aws_nat_gateway.nat_gw[0]")
            if initial_route:
                base_state.append("aws_route.private_internet[0]")

            post_state = list(base_state)
            if action == "disable" and apply_rc == 0:
                post_state = [
                    item
                    for item in base_state
                    if item not in {"aws_nat_gateway.nat_gw[0]", "aws_route.private_internet[0]"}
                ]
            if action == "enable" and apply_rc == 0:
                for item in ("aws_nat_gateway.nat_gw[0]", "aws_route.private_internet[0]"):
                    if item not in post_state:
                        post_state.append(item)

            attributes = {
                "aws_vpc.dr_vpc": {"id": EXPECTED["vpc_id"]},
                "aws_eip.nat_eip": {
                    "allocation_id": EXPECTED["allocation_id"],
                    "public_ip": EXPECTED["public_ip"],
                },
                "aws_subnet.public_subnet": {"id": EXPECTED["subnet_id"]},
                "aws_route_table.private_rt": {"id": EXPECTED["route_table_id"]},
                "aws_nat_gateway.nat_gw[0]": {
                    "id": "AWS_RESOURCE_ID_REDACTED",
                    "subnet_id": EXPECTED["subnet_id"],
                },
            }
            for address, values in (state_overrides or {}).items():
                attributes.setdefault(address, {}).update(values)

            state_json = {
                "version": 4,
                "resources": [
                    {
                        "address": address,
                        "mode": "managed",
                        "type": address.split(".")[0],
                        "name": address.split(".")[1].split("[")[0],
                        "instances": [{"attributes": values}],
                    }
                    for address, values in attributes.items()
                ],
            }
            fixture_path = base / "fixture.json"
            calls_path = base / "calls.log"
            fixture_path.write_text(
                json.dumps(
                    {
                        "state_list": base_state,
                        "post_state_list": post_state,
                        "state_json": state_json,
                        "apply_rc": apply_rc,
                        "calls": str(calls_path),
                        "nat_present": initial_nat,
                        "route_present": initial_route,
                        "post_nat_present": post_nat_present,
                        "nat_details": nat_details
                        or "AWS_RESOURCE_ID_REDACTED\tavailable\t%s\t%s\t%s\n"
                        % (
                            EXPECTED["subnet_id"],
                            EXPECTED["allocation_id"],
                            EXPECTED["public_ip"],
                        ),
                        "route_details": route_details
                        if route_details is not None
                        else "active AWS_RESOURCE_ID_REDACTED\n",
                    }
                ),
                encoding="utf-8",
            )

            bin_dir = base / "bin"
            bin_dir.mkdir()
            (bin_dir / "terraform").write_text(
                f"""#!/usr/bin/env python3
import json
fixture_path = {str(fixture_path)!r}
fixture = json.loads(open(fixture_path, encoding="utf-8").read())
args = __import__("sys").argv[1:]
if args[:2] == ["state", "list"]:
    print("\\n".join(fixture["state_list"]))
    raise SystemExit(0)
if args[:2] == ["state", "pull"]:
    print(json.dumps(fixture["state_json"]))
    raise SystemExit(0)
if args and args[0] == "apply":
    with open(fixture["calls"], "a", encoding="utf-8") as stream:
        stream.write(" ".join(args) + "\\n")
    if fixture["apply_rc"] == 0:
        fixture["state_list"] = fixture["post_state_list"]
        fixture["nat_present"] = (
            fixture["post_nat_present"]
            if fixture["post_nat_present"] is not None
            else "aws_nat_gateway.nat_gw[0]" in fixture["post_state_list"]
        )
        fixture["route_present"] = "aws_route.private_internet[0]" in fixture["post_state_list"]
        with open(fixture_path, "w", encoding="utf-8") as stream:
            json.dump(fixture, stream)
    raise SystemExit(fixture["apply_rc"])
raise SystemExit(2)
""",
                encoding="utf-8",
            )
            (bin_dir / "terraform").chmod(0o755)
            (bin_dir / "aws").write_text(
                f"""#!/usr/bin/env python3
import json
import sys
fixture_path = {str(fixture_path)!r}
fixture = json.loads(open(fixture_path, encoding="utf-8").read())
args = sys.argv[1:]
if args[:2] == ["ec2", "describe-addresses"]:
    print("{EXPECTED['allocation_id']}\\t{EXPECTED['public_ip']}\\tNone")
elif args[:2] == ["ec2", "describe-nat-gateways"]:
    if "--nat-gateway-ids" in args and fixture["nat_present"]:
        print(fixture["nat_details"], end="")
    elif "--nat-gateway-ids" not in args and fixture["nat_present"]:
        print(fixture["nat_details"], end="")
elif args[:2] == ["ec2", "describe-route-tables"]:
    if fixture["route_present"]:
        print(fixture["route_details"], end="")
elif args[:2] == ["ec2", "describe-instances"]:
    print("stopped")
elif args[:2] == ["ec2", "release-address"]:
    with open(fixture["calls"], "a", encoding="utf-8") as stream:
        stream.write("release-address\\n")
else:
    raise SystemExit(2)
""",
                encoding="utf-8",
            )
            (bin_dir / "aws").chmod(0o755)

            secret = base / "empty.env"
            secret.write_text("", encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "DR_REPO_ROOT": str(base),
                    "DR_TERRAFORM_DIR": terraform_dir_override or str(runtime_tf),
                    "DR_STATE_DIR": str(base / "state"),
                    "DR_STATE_OWNER": f"{pwd.getpwuid(os.getuid()).pw_name}:{grp.getgrgid(os.getgid()).gr_name}",
                    "DR_LOG_DIR": str(base / "logs"),
                    "DR_SECRET_FILE": str(secret),
                    "AWS_REGION": "ap-northeast-2",
                    "NAT_POSTCHECK_ATTEMPTS": "1",
                    "NAT_POSTCHECK_INTERVAL_SECONDS": "0",
                }
            )
            script = {
                "enable": nat_dir / "nat_enable.sh",
                "disable": nat_dir / "nat_disable.sh",
                "status": nat_dir / "nat_status.sh",
            }[action]
            result = subprocess.run(
                ["bash", str(script)],
                cwd=base,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            calls = calls_path.read_text(encoding="utf-8") if calls_path.exists() else ""
            return result, calls

    def test_canonical_terraform_directory_is_rejected(self):
        result, calls = self.run_nat(
            "enable", terraform_dir_override=str(ROOT / "infra/terraform")
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(" apply ", calls)
        output = result.stdout + result.stderr
        self.assertIn("NAT CONFIG ERROR", output)
        self.assertTrue(
            "DR_TERRAFORM_DIR" in output
            or "refusing to use" in output
            or "Canonical repository Terraform directory" in output
        )
        self.assertIn(str(ROOT / "infra/terraform"), output)

    def test_runtime_state_enable_reaches_terraform_only_after_preflight(self):
        result, calls = self.run_nat("enable", apply_rc=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("-target=aws_nat_gateway.nat_gw", calls)
        self.assertNotIn("Operational Terraform state mismatch", result.stdout + result.stderr)

    def test_identity_mismatches_fail_closed_before_apply(self):
        cases = (
            ("aws_vpc.dr_vpc", "id", "vpc-wrong"),
            ("aws_eip.nat_eip", "allocation_id", "eipalloc-wrong"),
            ("aws_eip.nat_eip", "public_ip", "198.51.100.10"),
            ("aws_subnet.public_subnet", "id", "subnet-wrong"),
            ("aws_route_table.private_rt", "id", "rtb-wrong"),
        )
        for address, attribute, value in cases:
            with self.subTest(address=address, attribute=attribute):
                result, calls = self.run_nat(
                    "enable", state_overrides={address: {attribute: value}}
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Operational Terraform state mismatch", result.stdout + result.stderr)
                self.assertNotIn(" apply ", calls)

    def test_already_ready_is_idempotent_and_does_not_apply(self):
        result, calls = self.run_nat("enable", initial_nat=True, initial_route=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NAT_ALREADY_READY=PASS", result.stdout + result.stderr)
        self.assertIn("NAT_ENABLE=PASS", result.stdout + result.stderr)
        self.assertNotIn(" apply ", calls)

    def test_enable_postcheck_mismatch_fails_without_success(self):
        result, calls = self.run_nat(
            "enable",
            nat_details="AWS_RESOURCE_ID_REDACTED\tavailable\t%s\t%s\t198.51.100.10\n"
            % (EXPECTED["subnet_id"], EXPECTED["allocation_id"]),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("public IP", result.stdout + result.stderr)
        self.assertNotIn("NAT_ENABLE=PASS", result.stdout + result.stderr)
        self.assertIn("-target=aws_route.private_internet", calls)

    def test_status_ready_off_and_drifted(self):
        ready, _ = self.run_nat("status", initial_nat=True, initial_route=True)
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("NAT_STATUS=READY", ready.stdout)

        off, _ = self.run_nat("status")
        self.assertEqual(off.returncode, 0, off.stdout + off.stderr)
        self.assertIn("NAT_STATUS=OFF", off.stdout)

        drifted, _ = self.run_nat(
            "status", initial_nat=True, initial_route=True, route_details="active nat-wrong\n"
        )
        self.assertNotEqual(drifted.returncode, 0)
        self.assertIn("NAT_STATUS=DRIFTED", drifted.stdout)

    def test_status_is_read_only(self):
        result, calls = self.run_nat("status", initial_nat=True, initial_route=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(" apply ", calls)
        self.assertNotIn("destroy", calls)

    def test_disable_preserves_eip_and_has_only_nat_targets(self):
        result, calls = self.run_nat("disable", initial_nat=True, initial_route=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NAT_DISABLE=PASS", result.stdout)
        self.assertIn("EIP_PRESERVED=PASS", result.stdout)
        self.assertIn("-target=aws_route.private_internet", calls)
        self.assertIn("-target=aws_nat_gateway.nat_gw", calls)
        self.assertNotIn("aws_eip.nat_eip", calls)
        self.assertNotIn("release-address", calls)
        self.assertNotIn("aws_instance", calls)
        self.assertNotIn("aws_key_pair", calls)

    def test_disable_already_off_is_idempotent(self):
        result, calls = self.run_nat("disable")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NAT_ALREADY_OFF=PASS", result.stdout)
        self.assertIn("EIP_PRESERVED=PASS", result.stdout)
        self.assertNotIn(" apply ", calls)

    def test_disable_fails_if_target_nat_still_exists_after_apply(self):
        result, calls = self.run_nat(
            "disable",
            initial_nat=True,
            initial_route=True,
            post_nat_present=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("still exists after disable", result.stdout + result.stderr)
        self.assertNotIn("NAT_DISABLE=PASS", result.stdout + result.stderr)
        self.assertIn("-target=aws_nat_gateway.nat_gw", calls)

    def test_nat_timer_and_legacy_finops_entrypoint_are_gone(self):
        self.assertFalse((ROOT / "operations/finops/nat_gateway_control.sh").exists())
        self.assertFalse((ROOT / "operations/finops/nat_schedule_enable.sh").exists())
        self.assertFalse((ROOT / "operations/finops/nat_schedule_disable.sh").exists())
        for tree in (ROOT / "operations", ROOT / "infra", ROOT / "docs"):
            for path in tree.rglob("*"):
                if (
                    not path.is_file()
                    or path.suffix in {".pyc", ".tfstate"}
                    or ".git" in path.parts
                    or ".terraform" in path.parts
                    or path.stat().st_size > 1_000_000
                ):
                    continue
                source = path.read_text(encoding="utf-8", errors="ignore")
                for stale in (
                    "nat_gateway_control.sh",
                    "nat_schedule_enable.sh",
                    "nat_schedule_disable.sh",
                    "hospital-dr-nat-enable.timer",
                    "hospital-dr-nat-disable.timer",
                    "hospital-dr-daily-eip-cleanup",
                ):
                    self.assertNotIn(stale, source, str(path))
        for unit in (
            ROOT / "infra/terraform/systemd/hospital-dr-nat-enable.service",
            ROOT / "infra/terraform/systemd/hospital-dr-nat-disable.service",
        ):
            source = unit.read_text(encoding="utf-8")
            self.assertIn("/home/cloud/hospital-dr-codex/operations/nat/", source)
            self.assertNotIn("ConditionPathExists=", source)
        shutdown = (ROOT / "infra/terraform/systemd/hospital-dr-nat-shutdown.service").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("ExecStop=", shutdown)

    def test_systemd_units_keep_operational_dir_and_manual_nat_entrypoints(self):
        marker = "Environment=DR_TERRAFORM_DIR=/home/cloud/hospital-dr-terraform"
        units = {
            "hospital-dr-nat-enable.service": "/home/cloud/hospital-dr-codex/operations/nat/nat_enable.sh",
            "hospital-dr-nat-disable.service": "/home/cloud/hospital-dr-codex/operations/nat/nat_disable.sh disable",
            "hospital-dr-nat-shutdown.service": "/bin/true",
            "hospital-dr-environment.service": "/home/cloud/hospital-dr-codex/operations/finops/dr_environment_control.sh",
        }
        for filename, entrypoint in units.items():
            source = (ROOT / "infra/terraform/systemd" / filename).read_text(encoding="utf-8")
            self.assertIn(marker, source, filename)
            self.assertIn(f"ExecStart={entrypoint}", source, filename)

    def test_finops_no_longer_owns_nat_mutation(self):
        for relative in (
            "operations/finops/dr_environment_control.sh",
            "operations/finops/dr_project_resume.sh",
            "operations/finops/post_demo_suspend.sh",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("nat_gateway_control.sh", source)
            self.assertNotIn("NAT_SCRIPT=", source)
            self.assertNotIn("NAT_CLEANUP_SCRIPT=", source)
            self.assertNotIn('"$NAT_DIR/nat_enable.sh"', source)
            self.assertNotIn('"$NAT_DIR/nat_disable.sh"', source)
        self.assertIn("DR_NAT_DIR", (ROOT / "operations/runtime_paths.sh").read_text())

    def test_cost_guard_has_no_nat_mutation_or_automatic_cleanup_schedule(self):
        lambda_source = (ROOT / "infra/terraform/lambda/dr_cost_guard.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("delete_nat_gateway", lambda_source)
        self.assertNotIn("release_address", lambda_source)
        terraform_source = (ROOT / "infra/terraform/main.tf").read_text(encoding="utf-8")
        self.assertNotIn('aws_cloudwatch_event_rule" "dr_cost_guard_cleanup"', terraform_source)


if __name__ == "__main__":
    unittest.main()
