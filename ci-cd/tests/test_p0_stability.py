import json
import os
import grp
import pwd
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHATOPS_DIR = ROOT / "operations" / "chatops"
RUNTIME_HELPER = ROOT / "operations" / "runtime" / "dr_state.sh"
DNS_HELPER = ROOT / "operations" / "failover" / "dns_transaction.sh"
FAILBACK_SCRIPT = ROOT / "operations" / "failover" / "failback.sh"
POST_DEMO_SCRIPT = ROOT / "operations" / "finops" / "post_demo_suspend.sh"
sys.path.insert(0, str(CHATOPS_DIR))

from chatops_core import (  # noqa: E402
    ApprovalStore,
    acquire_pipeline_file_lock,
    create_app,
    release_pipeline_file_lock,
    run_pipeline,
)


class RecordingSlackClient:
    def __init__(self, channel_id):
        self.channel_id = channel_id
        self.posts = []
        self.updates = []
        self.fail_post = False

    @property
    def available(self):
        return True

    def post_message(self, text, blocks):
        if self.fail_post:
            raise RuntimeError("fake Slack failure")
        result = {"ok": True, "channel": self.channel_id, "ts": f"9000.{len(self.posts) + 1}"}
        self.posts.append({"result": result, "text": text, "blocks": blocks})
        return result

    def update_message(self, channel_id, message_ts, text, blocks):
        self.updates.append({"channel": channel_id, "ts": message_ts, "text": text, "blocks": blocks})
        return {"ok": True}

    def delete_message(self, channel_id, message_ts):
        return {"ok": True}


class PersistentRuntimeStateTest(unittest.TestCase):
    def test_atomic_state_write_read_and_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp) / "runtime"
            env = os.environ.copy()
            env.update(
                {
                    "DR_STATE_DIR": str(state_dir),
                    "DR_STATE_OWNER": f"{pwd.getpwuid(os.getuid()).pw_name}:{grp.getgrgid(os.getgid()).gr_name}",
                    "DR_STATE_DIR_MODE": "0750",
                    "DR_STATE_FILE_MODE": "0640",
                }
            )
            script = f'''\
source "{RUNTIME_HELPER}"\ndr_state_prepare
test "$(dr_state_read)" = IDLE
dr_state_write FAILOVER_EXECUTING
test "$(dr_state_read)" = FAILOVER_EXECUTING
dr_state_write DR_ACTIVE
test "$(dr_state_read)" = DR_ACTIVE
dr_state_write PARTIAL_FAILURE
dr_state_mark_failure FAILED
test "$(dr_state_read)" = PARTIAL_FAILURE
dr_state_write DR_ACTIVE
printf '%s %s\n' "$(stat -c '%a' "$DR_STATE_DIR")" "$(stat -c '%a' "$DR_STATE_FILE")"
test -z "$(find "$DR_STATE_DIR" -maxdepth 1 -name '.dr_state.*' -print -quit)"
'''
            result = subprocess.run(
                ["bash", "-c", script],
                env=env,
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "750 640")
            self.assertEqual((state_dir / "dr_state").read_text(), "DR_ACTIVE\n")

    def test_stale_execution_is_failed_and_requires_fresh_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ApprovalStore(
                str(Path(temp) / "state.sqlite3"),
                initial_ttl=1000,
                approved_ttl=1000,
                execution_timeout=20,
                execution_lease_seconds=5,
            )
            state, reused = store.create_or_merge(
                "failover", {"incident_id": "old-event", "summary": "old"}, now=100
            )
            self.assertFalse(reused)
            result, _ = store.approve(state["request_id"], "failover", "U_ADMIN_1", now=101)
            self.assertEqual(result, "pending")
            result, executing = store.approve(
                state["request_id"], "failover", "U_ADMIN_2", now=102
            )
            self.assertEqual(result, "approved")
            old_execution_id = executing["execution_id"]
            self.assertEqual(executing["status"], "EXECUTING")

            recovered = store.recover_stale_executions(now=108)
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["status"], "FAILED")
            self.assertEqual(recovered[0]["result"], "stale_execution_recovered")
            self.assertEqual(store.finish(state["request_id"], True)["status"], "FAILED")

            fresh, reused = store.create_or_merge(
                "failover", {"incident_id": "new-event", "summary": "new"}, now=109
            )
            self.assertFalse(reused)
            self.assertNotEqual(fresh["request_id"], state["request_id"])
            self.assertEqual(fresh["approvers"], [])
            result, _ = store.approve(fresh["request_id"], "failover", "U_ADMIN_1", now=110)
            self.assertEqual(result, "pending")
            result, fresh_executing = store.approve(
                fresh["request_id"], "failover", "U_ADMIN_2", now=111
            )
            self.assertEqual(result, "approved")
            self.assertNotEqual(fresh_executing["execution_id"], old_execution_id)

    def test_pipeline_total_timeout_marks_request_failed(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            script_root = temp_dir / "scripts"
            script_root.mkdir()
            workflow = script_root / "full_failover.sh"
            workflow.write_text("#!/bin/sh\nsleep 5\n")
            workflow.chmod(0o755)
            state_db = str(temp_dir / "state.sqlite3")
            failover = RecordingSlackClient("C_FAILOVER")
            failback = RecordingSlackClient("C_FAILBACK")
            app = create_app(
                default_state_db=state_db,
                default_secret_file=str(temp_dir / "missing.env"),
                default_script_root=str(script_root),
                test_config={
                    "TESTING": True,
                    "CHATOPS_STATE_DB": state_db,
                    "DR_SCRIPT_ROOT": str(script_root),
                    "SLACK_CLIENTS": {"failover": failover, "failback": failback},
                    "ADMIN_USER_IDS": {"U_ADMIN_1", "U_ADMIN_2"},
                    "VERIFY_SLACK_SIGNATURE": False,
                    "CHATOPS_EXECUTION_TIMEOUT_SECONDS": 1.0,
                    "CHATOPS_EXECUTION_HEARTBEAT_SECONDS": 1.0,
                    "CHATOPS_EXECUTION_LEASE_SECONDS": 10.0,
                    "CHATOPS_EXECUTION_LOCK_FILE": str(temp_dir / "pipeline.lock"),
                },
            )
            store = app.extensions["approval_store"]
            state, _ = store.create_or_merge(
                "failover", {"incident_id": "timeout-event", "summary": "timeout"}
            )
            store.approve(state["request_id"], "failover", "U_ADMIN_1")
            result, executing = store.approve(state["request_id"], "failover", "U_ADMIN_2")
            self.assertEqual(result, "approved")
            started = time.monotonic()
            run_pipeline(app, executing["request_id"], "failover", "U_ADMIN_2", "")
            elapsed = time.monotonic() - started
            finished = store.get(executing["request_id"])
            self.assertLess(elapsed, 4)
            self.assertEqual(finished["status"], "FAILED")
            self.assertEqual(finished["result"], "timeout")

    def test_pipeline_lock_is_shared_with_other_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            lock_path = str(Path(temp) / "pipeline.lock")
            first = acquire_pipeline_file_lock(lock_path)
            self.assertIsNotNone(first)
            try:
                second = acquire_pipeline_file_lock(lock_path)
                self.assertIsNone(second)
            finally:
                release_pipeline_file_lock(first)


class ZabbixRetryContractTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_dir = Path(self.temp_dir.name)
        state_db = str(temp_dir / "state.sqlite3")
        self.failover = RecordingSlackClient("C_FAILOVER")
        self.failback = RecordingSlackClient("C_FAILBACK")
        self.alert = RecordingSlackClient("C_ALERT")
        self.app = create_app(
            default_state_db=state_db,
            default_secret_file=str(temp_dir / "missing.env"),
            default_script_root="/nonexistent/mock-only",
            test_config={
                "TESTING": True,
                "CHATOPS_STATE_DB": state_db,
                "SLACK_CLIENTS": {"failover": self.failover, "failback": self.failback},
                "SLACK_ALERT_CLIENT": self.alert,
                "ADMIN_USER_IDS": {"U_ADMIN_1", "U_ADMIN_2"},
                "VERIFY_SLACK_SIGNATURE": False,
                "PIPELINE_LAUNCHER": lambda request_id, action, approver: None,
            },
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_slack_failure_returns_non_2xx_and_event_is_retryable(self):
        payload = {
            "EventID": "retry-event-1",
            "TriggerID": "trigger-1",
            "Host": "DB_Server",
            "Severity": "HIGH",
            "Status": "PROBLEM",
            "Message": "database unavailable",
        }
        self.alert.fail_post = True
        failed = self.client.post("/zabbix/alert", json=payload)
        self.assertGreaterEqual(failed.status_code, 500)
        self.assertFalse(
            self.app.extensions["chatops_audit"].has_alert_incident("zabbix", "retry-event-1")
        )

        self.alert.fail_post = False
        succeeded = self.client.post("/zabbix/alert", json=payload)
        self.assertEqual(succeeded.status_code, 200)
        self.assertTrue(succeeded.get_json()["alert_sent"])
        duplicate = self.client.post("/zabbix/alert", json=payload)
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.get_json()["duplicate"])
        self.assertEqual(len(self.alert.posts), 1)

    def test_escalation_failure_does_not_register_event_for_retry(self):
        payload = {
            "EventID": "retry-event-escalation",
            "TriggerID": "trigger-2",
            "Host": "DB_Server",
            "Severity": "CRITICAL",
            "Status": "PROBLEM",
            "Message": "database destruction signal",
            "DR_Escalate": "1",
        }
        self.failover.fail_post = True
        failed = self.client.post("/zabbix/alert", json=payload)
        self.assertGreaterEqual(failed.status_code, 500)
        self.assertFalse(
            self.app.extensions["chatops_audit"].has_alert_incident(
                "zabbix", "retry-event-escalation"
            )
        )

        self.failover.fail_post = False
        succeeded = self.client.post("/zabbix/alert", json=payload)
        self.assertEqual(succeeded.status_code, 200)
        self.assertTrue(succeeded.get_json()["dr_escalated"])
        self.assertTrue(
            self.app.extensions["chatops_audit"].has_alert_incident(
                "zabbix", "retry-event-escalation"
            )
        )


class FakeCloudflareContractTest(unittest.TestCase):
    def write_fake_curl(self, temp_dir, mode):
        bin_dir = Path(temp_dir) / "bin"
        bin_dir.mkdir(exist_ok=True)
        curl = bin_dir / "curl"
        curl.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
log=\"${{FAKE_CURL_LOG:?}}\"
method=GET
url=\"\"
args=(\"$@\")
for ((i=0; i<${{#args[@]}}; i++)); do
  if [ \"${{args[i]}}\" = \"-X\" ]; then
    method=\"${{args[i + 1]}}\"
  elif [[ \"${{args[i]}}\" == http://* || \"${{args[i]}}\" == https://* ]]; then
    url=\"${{args[i]}}\"
  fi
done
printf '%s %s\\n' \"$method\" \"$url\" >> \"$log\"
if [ \"$method\" = PUT ]; then
  put_count=\"$(awk '$1 == \"PUT\" {{n++}} END {{print n + 0}}' \"$log\")\"
  if [ \"{mode}\" = partial ] && [ \"$put_count\" -eq 2 ]; then
    printf '%s' '{{\"success\":false,\"errors\":[{{\"code\":1000}}]}}'
  else
    printf '%s' '{{\"success\":true,\"result\":{{}}}}'
  fi
  exit 0
fi
if [[ \"$url\" == https://api.cloudflare.com/* ]]; then
  name=unknown
  for arg in \"${{args[@]}}\"; do
    case \"$arg\" in
      name=*) name=\"${{arg#name=}}\" ;;
    esac
  done
  printf '{{\"success\":true,\"result\":[{{\"id\":\"id-%s\",\"type\":\"A\",\"name\":\"%s\",\"content\":\"old-%s\",\"ttl\":1,\"proxied\":true}}]}}' \"$name\" \"$name\" \"$name\"
  exit 0
fi
if [ \"{mode}\" = health-failure ] && [[ \"$url\" == https://hospital-web.pp.ua/* || \"$url\" == https://emr.hospital-web.pp.ua/* ]]; then
  exit 1
fi
exit 0
"""
        )
        curl.chmod(0o755)
        return bin_dir

    def snapshot_dir(self, temp_dir):
        snapshot_dir = Path(temp_dir) / "snapshots"
        snapshot_dir.mkdir()
        for name, record_id in (("web", "web-id"), ("emr", "emr-id")):
            (snapshot_dir / f"{name}.json").write_text(
                json.dumps(
                    {
                        "id": record_id,
                        "payload": {
                            "type": "A",
                            "name": f"{name}.hospital-web.pp.ua",
                            "content": f"old-{name}",
                            "ttl": 1,
                            "proxied": True,
                        },
                    }
                )
            )
        return snapshot_dir

    def test_dns_snapshot_contains_full_restore_fields_and_is_private(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            bin_dir = self.write_fake_curl(temp_dir, "none")
            snapshot = temp_dir / "snapshots" / "web.json"
            snapshot.parent.mkdir()
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{bin_dir}:{environment['PATH']}",
                    "FAKE_CURL_LOG": str(temp_dir / "curl.log"),
                    "CF_ZONE_ID": "fake-zone",
                    "CLOUDFLARE_API_TOKEN": "not-used-by-test",
                }
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1"; dr_dns_snapshot_record "$2" "$3"',
                    "bash",
                    str(DNS_HELPER),
                    "hospital-web.pp.ua",
                    str(snapshot),
                ],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = json.loads(snapshot.read_text())
            self.assertEqual(
                set(saved["payload"]),
                {"type", "name", "content", "ttl", "proxied"},
            )
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)

    def run_dns_apply(self, temp_dir, mode="partial"):
        bin_dir = self.write_fake_curl(temp_dir, mode)
        snapshot_dir = self.snapshot_dir(temp_dir)
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "FAKE_CURL_LOG": str(Path(temp_dir) / "curl.log"),
                "CF_ZONE_ID": "fake-zone",
                "CLOUDFLARE_API_TOKEN": "not-used-by-test",
            }
        )
        command = [
            "bash",
            "-c",
            'source "$1"; shift; dr_dns_apply_records "$@"',
            "bash",
            str(DNS_HELPER),
            str(snapshot_dir),
            "new-web-id",
            '{"type":"A","name":"hospital-web.pp.ua","content":"new-web","ttl":1,"proxied":true}',
            "cutover:web",
            "new-emr-id",
            '{"type":"A","name":"emr.hospital-web.pp.ua","content":"new-emr","ttl":1,"proxied":true}',
            "cutover:emr",
        ]
        return subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=False), env

    def test_partial_dns_update_rolls_back_all_records(self):
        with tempfile.TemporaryDirectory() as temp:
            result, env = self.run_dns_apply(temp, mode="partial")
            self.assertEqual(result.returncode, 1, result.stderr)
            calls = Path(env["FAKE_CURL_LOG"]).read_text().splitlines()
            self.assertEqual(len([line for line in calls if line.startswith("PUT ")]), 4)
            self.assertIn("rollback:", result.stdout)

    def test_public_health_failure_rolls_back_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            bin_dir = self.write_fake_curl(temp_dir, "health-failure")
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "FAKE_CURL_LOG": str(temp_dir / "curl.log"),
                    "DR_STATE_DIR": str(temp_dir / "state"),
                    "DR_STATE_OWNER": f"{pwd.getpwuid(os.getuid()).pw_name}:{grp.getgrgid(os.getgid()).gr_name}",
                    "DR_LOG_DIR": str(temp_dir / "logs"),
                    "CF_ZONE_ID": "fake-zone",
                    "ONPREM_WEB_IP": "203.0.113.10",
                    "ONPREM_HEALTH_ATTEMPTS": "1",
                    "PUBLIC_HEALTH_ATTEMPTS": "1",
                    "PUBLIC_HEALTH_INTERVAL_SECONDS": "0",
                    "FAILBACK_TARGET_MODE": "direct",
                    "DR_DOMAIN": "hospital-web.pp.ua",
                    "EMR_DOMAIN": "emr.hospital-web.pp.ua",
                    "CF_TOKEN_PARAM_NAME": "/fake/cloudflare/token",
                    "DR_REPO_ROOT": str(ROOT),
                }
            )
            aws = bin_dir / "aws"
            aws.write_text("#!/bin/sh\nprintf '%s\\n' placeholder\n")
            aws.chmod(0o755)
            result = subprocess.run(
                ["bash", str(FAILBACK_SCRIPT)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((temp_dir / "state" / "dr_state").read_text().strip(), "FAILED")
            calls = (temp_dir / "curl.log").read_text().splitlines()
            self.assertEqual(len([line for line in calls if line.startswith("PUT ")]), 4)
            self.assertIn("attempting rollback", result.stdout)


class FinOpsFailureContractTest(unittest.TestCase):
    def write_script(self, path, exit_code):
        path.write_text(f"#!/bin/sh\necho fake-step-$0-$*\nexit {exit_code}\n")
        path.chmod(0o755)

    def test_direct_nat_failure_is_partial_and_nonzero(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            bin_dir = temp_dir / "bin"
            bin_dir.mkdir()
            terraform = bin_dir / "terraform"
            calls = temp_dir / "terraform.calls"
            terraform.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {calls}\nexit 1\n")
            terraform.chmod(0o755)
            state_dir = temp_dir / "state"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "DR_REPO_ROOT": str(ROOT),
                    "DR_STATE_DIR": str(state_dir),
                    "DR_STATE_OWNER": f"{pwd.getpwuid(os.getuid()).pw_name}:{grp.getgrgid(os.getgid()).gr_name}",
                    "DR_LOG_DIR": str(temp_dir / "logs"),
                    "DR_TERRAFORM_DIR": str(temp_dir),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "operations/nat/nat_enable.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((state_dir / "dr_state").read_text().strip(), "PARTIAL_FAILURE")
            self.assertIn("DR_TERRAFORM_DIR must be /home/cloud/hospital-dr-terraform", result.stdout + result.stderr)
            self.assertNotIn("-target=aws_nat_gateway.nat_gw", calls.read_text() if calls.exists() else "")
            self.assertNotIn("[NAT ENABLED]", result.stdout + result.stderr)

    def test_direct_nat_disable_failure_is_partial_without_ssh_key(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            bin_dir = temp_dir / "bin"
            bin_dir.mkdir()
            terraform = bin_dir / "terraform"
            calls = temp_dir / "terraform.calls"
            terraform.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {calls}\nexit 1\n")
            terraform.chmod(0o755)
            state_dir = temp_dir / "state"
            env = os.environ.copy()
            env.pop("DR_SSH_PRIVATE_KEY", None)
            env.pop("SSH_KEY", None)
            env.pop("DR_SSH_KEY", None)
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "DR_REPO_ROOT": str(ROOT),
                    "DR_STATE_DIR": str(state_dir),
                    "DR_STATE_OWNER": f"{pwd.getpwuid(os.getuid()).pw_name}:{grp.getgrgid(os.getgid()).gr_name}",
                    "DR_LOG_DIR": str(temp_dir / "logs"),
                    "DR_TERRAFORM_DIR": str(temp_dir),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "operations/nat/nat_disable.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((state_dir / "dr_state").read_text().strip(), "PARTIAL_FAILURE")
            self.assertIn("DR_TERRAFORM_DIR must be /home/cloud/hospital-dr-terraform", result.stdout + result.stderr)
            call = calls.read_text() if calls.exists() else ""
            self.assertNotIn("-target=aws_route.private_internet", call)
            self.assertNotIn("-target=aws_nat_gateway.nat_gw", call)
            self.assertNotIn("[NAT DISABLED]", result.stdout + result.stderr)

    def test_nat_lifecycle_targets_do_not_include_ec2_or_key_pair(self):
        source = (ROOT / "operations/nat/nat_common.sh").read_text()
        self.assertIn("-target='aws_eip.nat_eip'", source)
        self.assertIn("-target='aws_nat_gateway.nat_gw'", source)
        self.assertIn("-target='aws_route.private_internet'", source)
        self.assertNotIn("aws_instance", source)
        self.assertNotIn("aws_key_pair", source)

    def test_post_demo_partial_failure_is_nonzero_and_never_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            bin_dir = temp_dir / "bin"
            bin_dir.mkdir()
            aws = bin_dir / "aws"
            aws.write_text("#!/bin/sh\nexit 0\n")
            aws.chmod(0o755)
            failback = temp_dir / "failback.sh"
            status = temp_dir / "dr_environment_control.sh"
            self.write_script(failback, 1)
            self.write_script(status, 0)
            state_dir = temp_dir / "state"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "DR_REPO_ROOT": str(ROOT),
                    "DR_STATE_DIR": str(state_dir),
                    "DR_STATE_OWNER": f"{pwd.getpwuid(os.getuid()).pw_name}:{grp.getgrgid(os.getgid()).gr_name}",
                    "DR_LOG_DIR": str(temp_dir / "logs"),
                    "TF_DIR": str(temp_dir),
                    "FAILBACK_SCRIPT": str(failback),
                    "STATUS_SCRIPT": str(status),
                }
            )
            result = subprocess.run(
                ["bash", str(POST_DEMO_SCRIPT)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((state_dir / "dr_state").read_text().strip(), "PARTIAL_FAILURE")
            output = result.stdout + result.stderr
            self.assertIn("[SUSPEND PARTIAL_FAILURE]", output)
            self.assertNotIn("[SUSPEND COMPLETE]", output)


class FinOpsEnvironmentFailureTest(unittest.TestCase):
    def test_ec2_stop_failure_is_partial_and_nonzero(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            bin_dir = temp_dir / "bin"
            bin_dir.mkdir()
            aws = bin_dir / "aws"
            aws.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
case " $* " in
  *" sts get-caller-identity "*) exit 0 ;;
  *" get-parameter "*) printf '%s\\n' placeholder ;;
  *" describe-instances "*) printf 'i-web\\ni-doc\\ni-db\\n' ;;
  *" stop-instances "*) exit 1 ;;
  *" instance-stopped "*) exit 0 ;;
  *" describe-addresses "*) exit 0 ;;
  *) exit 0 ;;
esac
"""
            )
            aws.chmod(0o755)
            terraform = bin_dir / "terraform"
            terraform.write_text("#!/bin/sh\nexit 0\n")
            terraform.chmod(0o755)
            curl = bin_dir / "curl"
            curl.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
url=""
for arg in "$@"; do
  case "$arg" in
    http://*|https://*) url="$arg" ;;
  esac
done
if [[ "$url" == https://api.cloudflare.com/* ]]; then
  printf '%s' '{"success":true,"result":[{"id":"fake-record","type":"A","name":"hospital-web.pp.ua","content":"old-origin","ttl":1,"proxied":true}]}'
fi
exit 0
"""
            )
            curl.chmod(0o755)
            state_dir = temp_dir / "state"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "DR_REPO_ROOT": str(ROOT),
                    "DR_STATE_DIR": str(state_dir),
                    "DR_STATE_OWNER": f"{pwd.getpwuid(os.getuid()).pw_name}:{grp.getgrgid(os.getgid()).gr_name}",
                    "DR_LOG_DIR": str(temp_dir / "logs"),
                    "TF_DIR": str(temp_dir),
                    "CF_ZONE_ID": "fake-zone",
                    "ONPREM_WEB_HEALTH_URL": "http://onprem/",
                    "ONPREM_EMR_HEALTH_URL": "http://onprem/emr/",
                    "ONPREM_HEALTH_ATTEMPTS": "1",
                    "PUBLIC_HEALTH_ATTEMPTS": "1",
                    "PUBLIC_HEALTH_INTERVAL_SECONDS": "0",
                    "DR_DNS_TX_DIR": str(temp_dir / "dns"),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "operations/finops/dr_environment_control.sh"), "stop"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((state_dir / "dr_state").read_text().strip(), "PARTIAL_FAILURE")
            output = result.stdout + result.stderr
            self.assertIn("[DR STOP ERROR] Failed to request EC2 stop.", output)
            self.assertNotIn("[DR STOPPED]", output)


    def test_cost_only_stop_skips_dns_and_returns_idle(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            bin_dir = temp_dir / "bin"
            bin_dir.mkdir()
            aws = bin_dir / "aws"
            aws.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
case " $* " in
  *" sts get-caller-identity "*) exit 0 ;;
  *" describe-instances "*) printf 'i-web\\ni-doc\\ni-db\\n' ;;
  *" stop-instances "*) exit 0 ;;
  *" instance-stopped "*) exit 0 ;;
  *" describe-addresses "*) exit 0 ;;
  *) exit 0 ;;
esac
"""
            )
            aws.chmod(0o755)
            curl = bin_dir / "curl"
            curl.write_text("#!/bin/sh\necho unexpected_dns_health_call >&2\nexit 42\n")
            curl.chmod(0o755)
            status = temp_dir / "dr_environment_control-status.sh"
            status.write_text("#!/bin/sh\nexit 0\n")
            status.chmod(0o755)
            state_dir = temp_dir / "state"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "DR_REPO_ROOT": str(ROOT),
                    "DR_STATE_DIR": str(state_dir),
                    "DR_STATE_OWNER": f"{pwd.getpwuid(os.getuid()).pw_name}:{grp.getgrgid(os.getgid()).gr_name}",
                    "DR_LOG_DIR": str(temp_dir / "logs"),
                    "TF_DIR": str(temp_dir),
                    "STATUS_SCRIPT": str(status),
                    "SKIP_DNS": "true",
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "operations/finops/dr_environment_control.sh"), "stop"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((state_dir / "dr_state").read_text().strip(), "IDLE")
            output = result.stdout + result.stderr
            self.assertIn("SKIP_DNS=true", output)
            self.assertNotIn("unexpected_dns_health_call", output)

    def test_cost_only_resume_keeps_idle_without_dns_cutover(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            bin_dir = temp_dir / "bin"
            bin_dir.mkdir()
            aws = bin_dir / "aws"
            aws.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
case " $* " in
  *" sts get-caller-identity "*) exit 0 ;;
  *) exit 0 ;;
esac
"""
            )
            aws.chmod(0o755)
            environment = temp_dir / "dr_environment_control.sh"
            environment.write_text("#!/bin/sh\nexit 0\n")
            environment.chmod(0o755)
            state_dir = temp_dir / "state"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "DR_REPO_ROOT": str(ROOT),
                    "DR_STATE_DIR": str(state_dir),
                    "DR_STATE_OWNER": f"{pwd.getpwuid(os.getuid()).pw_name}:{grp.getgrgid(os.getgid()).gr_name}",
                    "DR_LOG_DIR": str(temp_dir / "logs"),
                    "ENV_SCRIPT": str(environment),
                    "SKIP_DNS": "true",
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "operations/finops/dr_project_resume.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((state_dir / "dr_state").read_text().strip(), "IDLE")
            output = result.stdout + result.stderr
            self.assertIn("SKIP_DNS=true", output)
            self.assertNotIn("DR_ACTIVE", output)


class StaticP0ContractTest(unittest.TestCase):
    def test_failover_and_failback_share_a_persistent_workflow_lock(self):
        entrypoints = [
            ROOT / "operations/failover/full_failover.sh",
            ROOT / "operations/failover/full_failback.sh",
            ROOT / "infra/terraform/run_demo.sh",
            ROOT / "operations/failover/failback.sh",
            ROOT / "operations/finops/dr_environment_control.sh",
            ROOT / "operations/nat/nat_enable.sh",
            ROOT / "operations/finops/dr_project_resume.sh",
            ROOT / "operations/finops/post_demo_suspend.sh",
        ]
        for path in entrypoints:
            source = path.read_text()
            if path.parent == ROOT / "operations/nat":
                self.assertIn("nat_common.sh", source, str(path))
                continue
            self.assertIn("DR_WORKFLOW_LOCK_FILE", source, str(path))
            self.assertIn("DR_WORKFLOW_LOCK_HELD", source, str(path))
            if path.name == "nat_enable.sh":
                common = (ROOT / "operations/nat/nat_common.sh").read_text()
                self.assertIn("NAT_STATE_FAILURE_STATUS=PARTIAL_FAILURE", common)
                self.assertIn("dr_state_mark_failure", common)
            if path.name == "dr_environment_control.sh":
                self.assertNotIn("mapfile -t ids < <(instance_ids)", source)
                self.assertIn('if ! instance_list="$(instance_ids)";', source)
        for path in (
            ROOT / "operations/finops/dr_environment_control.sh",
            ROOT / "operations/finops/dr_project_resume.sh",
            ROOT / "operations/finops/post_demo_suspend.sh",
        ):
            self.assertIn("SKIP_DNS", path.read_text(), str(path))
        self.assertIn("export DR_FAILBACK_CONTROLLER=1", (ROOT / "operations/finops/post_demo_suspend.sh").read_text())

        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp) / "runtime"
            script = f"""set -euo pipefail
DR_STATE_DIR="{state_dir}"
export DR_STATE_DIR
source "{RUNTIME_HELPER}"
dr_state_prepare
dr_workflow_lock_prepare
export DR_WORKFLOW_LOCK_FILE
exec 8>"$DR_WORKFLOW_LOCK_FILE"
flock -n 8
if bash -c 'exec 9>"$DR_WORKFLOW_LOCK_FILE"; flock -n 9'; then
  exit 1
fi
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_old_ephemeral_failover_marker_is_gone_from_runtime_paths(self):
        paths = [
            ROOT / "operations/db/zabbix_failback_guard.sh",
            ROOT / "operations/failover/failback.sh",
            ROOT / "operations/failover/full_failover.sh",
            ROOT / "operations/failover/full_failback.sh",
            ROOT / "operations/finops/dr_environment_control.sh",
            ROOT / "operations/finops/post_demo_suspend.sh",
            ROOT / "operations/finops/dr_project_resume.sh",
            ROOT / "infra/terraform/run_demo.sh",
        ]
        source = "\n".join(path.read_text() for path in paths)
        self.assertNotIn("hospital-dr-failover." + "active", source)
        self.assertNotIn("FAILOVER_" + "ACTIVE_FILE", source)
        self.assertIn("dr_state_write DR_ACTIVE", source)

    def test_systemd_units_provision_persistent_runtime_directory(self):
        service = (ROOT / "operations/chatops/systemd/hospital-dr-chatops.service").read_text()
        environment = (ROOT / "infra/terraform/systemd/hospital-dr-environment.service").read_text()
        self.assertIn("StateDirectory=hospital-dr", service)
        self.assertIn("Environment=CHATOPS_EXECUTION_LOCK_FILE=/var/lib/hospital-dr/chatops.pipeline.lock", service)
        self.assertIn("StateDirectory=hospital-dr", environment)

    def test_zabbix_transport_already_preserves_failure_status(self):
        media_type = (ROOT / "operations/chatops/zabbix_media_type.js").read_text()
        proxy = (ROOT / "operations/chatops/zabbix_host_proxy.py").read_text()
        self.assertIn("req.getStatus() < 200 || req.getStatus() >= 300", media_type)
        self.assertIn("HTTP/1.1 {status}", proxy)


if __name__ == "__main__":
    unittest.main()
