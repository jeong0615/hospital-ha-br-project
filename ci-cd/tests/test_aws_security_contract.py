import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


class AwsEndpointSecurityContractTest(unittest.TestCase):
    def test_bootstrap_is_non_destructive_and_keeps_agent_ports_local(self):
        source = (ROOT / "operations/aws/aws_endpoint_security_bootstrap.sh").read_text()
        self.assertIn('"Server": "127.0.0.1"', source)
        self.assertIn("ListenIP", source)
        self.assertIn("agent-auth", source)
        self.assertIn("aws_endpoint_security_ssm_controller_candidate", (ROOT / "infra/terraform/iam_least_privilege_candidates.tf").read_text())
        self.assertNotIn("0.0.0.0/0", source)
        self.assertNotIn("docker.sock", source)
        self.assertNotIn("CHANGE REPLICATION SOURCE", source)

    def test_controller_uses_ssm_and_fixed_instance_contract(self):
        source = (ROOT / "operations/aws/apply_endpoint_security.sh").read_text()
        self.assertIn("AWS-RunShellScript", source)
        self.assertIn("AWS_RESOURCE_ID_REDACTED:web:aws-dr-web:10.20.10.10", source)
        self.assertIn("AWS_RESOURCE_ID_REDACTED:emr:aws-dr-emr:10.20.20.10", source)
        self.assertIn("AWS_RESOURCE_ID_REDACTED:db:aws-dr-db:10.20.30.10", source)
        self.assertNotIn("start-instances", source)
        self.assertIn("AWS_ZABBIX_EXTERNAL_PATH_ENABLED", source)
        self.assertIn("AWS_WAZUH_EXTERNAL_PATH_ENABLED", source)
        self.assertIn("DISABLED_BY_DESIGN", source)

    def test_endpoint_bootstrap_is_disabled_by_default(self):
        source = (ROOT / "operations/aws/aws_endpoint_security_bootstrap.sh").read_text()
        self.assertIn("AWS_ZABBIX_EXTERNAL_PATH_ENABLED:-false", source)
        self.assertIn("AWS_WAZUH_EXTERNAL_PATH_ENABLED:-false", source)
        self.assertIn("reason=MSP_BEHIND_UNCONTROLLED_UPSTREAM_NAT", source)

    def test_zabbix_active_hosts_keep_real_private_interface_addresses(self):
        source = (ROOT / "operations/zabbix/configure_aws_hosts.py").read_text()
        self.assertIn('"aws-dr-web": {"name": "aws-dr-web", "ip": "10.20.10.10"', source)
        self.assertIn('"aws-dr-emr": {"name": "aws-dr-emr", "ip": "10.20.20.10"', source)
        self.assertIn('"aws-dr-db": {"name": "aws-dr-db", "ip": "10.20.30.10"', source)
        self.assertIn('"hostinterface.update"', source)
        self.assertNotIn('"ip": "127.0.0.1"', source)
        self.assertIn("AWS_ZABBIX_MONITORING_ENABLED", source)
        self.assertIn("AWS_ZABBIX_MONITORING_RETIRED", source)

    def test_aws_server_monitoring_retirement_contract_is_present(self):
        source = (ROOT / "operations/zabbix/retire_decommissioned_monitoring.py").read_text()
        for marker in (
            'TEST_HOST = "DR-CHATOPS-TEST"',
            'AWS_HOSTS = ("aws-dr-web", "aws-dr-emr", "aws-dr-db")',
            'AWS_GROUP = "AWS DR"',
            '"host.delete"',
            '"environment") == "aws"',
            '"dr") == "true"',
            "--confirm",
        ):
            self.assertIn(marker, source)
        dashboard = json.loads(
            (ROOT / "monitoring/grafana/provisioning/dashboards/json/hospital-aws-dr-security.json").read_text()
        )
        self.assertFalse(
            any("alexanderzobnin-zabbix-datasource" in json.dumps(panel) for panel in dashboard["panels"])
        )


class AwsCloudTrailCollectorTest(unittest.TestCase):
    def test_cloudtrail_writer_role_is_index_scoped_without_delete_or_cluster_admin(self):
        role = json.loads((ROOT / "monitoring/wazuh/aws-cloudtrail-writer-role.json").read_text())
        self.assertEqual(role["role"], "hospital_aws_cloudtrail_writer")
        self.assertEqual(role["cluster_permissions"], [])
        permission = role["index_permissions"][0]
        self.assertEqual(permission["index_patterns"], ["hospital-aws-cloud-events"])
        actions = set(permission["allowed_actions"])
        self.assertIn("indices:data/write/index", actions)
        self.assertNotIn("delete", actions)
        self.assertNotIn("manage", actions)
        self.assertNotIn("*", actions)

    def setUp(self):
        import sys

        sys.path.insert(0, str(ROOT / "operations/security"))
        from aws_cloudtrail_collector import normalize_event

        self.normalize_event = normalize_event

    def test_normalizes_high_risk_event_without_raw_cloudtrail_payload(self):
        event = {
            "EventId": "event-1",
            "EventTime": "2026-09-20T00:00:00Z",
            "EventName": "AuthorizeSecurityGroupIngress",
            "CloudTrailEvent": json.dumps(
                {
                    "eventID": "event-1",
                    "eventTime": "2026-09-20T00:00:00Z",
                    "eventName": "AuthorizeSecurityGroupIngress",
                    "eventSource": "ec2.amazonaws.com",
                    "awsRegion": "ap-northeast-2",
                    "sourceIPAddress": "192.0.2.10",
                    "userIdentity": {"type": "IAMUser", "arn": "AWS_ARN_REDACTED"},
                    "requestParameters": {"ipPermissions": "redacted-by-normalizer"},
                }
            ),
        }
        normalized = self.normalize_event(event)
        self.assertEqual(normalized["severity"], "HIGH")
        self.assertEqual(normalized["category"], "configuration_or_control_plane")
        self.assertTrue(normalized["security_relevant"])
        self.assertNotIn("requestParameters", normalized)

    def test_read_only_event_is_not_high(self):
        normalized = self.normalize_event(
            {
                "EventId": "event-2",
                "EventTime": "2026-09-20T00:00:00Z",
                "EventName": "DescribeInstances",
                "CloudTrailEvent": json.dumps(
                    {
                        "eventTime": "2026-09-20T00:00:00Z",
                        "eventName": "DescribeInstances",
                        "eventSource": "ec2.amazonaws.com",
                        "awsRegion": "ap-northeast-2",
                        "readOnly": True,
                        "userIdentity": {"type": "AssumedRole", "arn": "AWS_ARN_REDACTED"},
                    }
                ),
            }
        )
        self.assertEqual(normalized["severity"], "INFO")
        self.assertFalse(normalized["security_relevant"])

    def test_lookup_command_is_read_only(self):
        from aws_cloudtrail_collector import lookup_events

        runner = Mock(return_value=subprocess.CompletedProcess([], 0, '{"Events": []}', ""))
        result = lookup_events(
            "ap-northeast-2",
            __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            runner=runner,
        )
        self.assertEqual(result, [])
        command = runner.call_args.args[0]
        self.assertEqual(command[:3], ["aws", "cloudtrail", "lookup-events"])
        mutating_tokens = {"create", "delete", "put-object", "start-instances", "stop-instances", "terminate-instances"}
        self.assertTrue(mutating_tokens.isdisjoint(set(command)))

    def test_collect_writes_only_normalized_documents_and_state(self):
        from aws_cloudtrail_collector import collect

        class FakeWriter:
            def __init__(self):
                self.indexes = []
                self.documents = []

            def ensure_index(self, name, mapping):
                self.indexes.append((name, mapping))

            def index_document(self, name, document_id, document):
                self.documents.append((name, document_id, document))

        with tempfile.TemporaryDirectory() as directory:
            writer = FakeWriter()
            result = collect(
                region="ap-northeast-2",
                lookback_minutes=60,
                state_file=str(Path(directory) / "state.json"),
                max_results=10,
                aws_profile="",
                dry_run=False,
                writer=writer,
                lookup=lambda *args, **kwargs: [
                    {
                        "EventId": "event-3",
                        "EventTime": "2026-09-20T00:00:00Z",
                        "EventName": "StartInstances",
                        "CloudTrailEvent": json.dumps(
                            {
                                "eventID": "event-3",
                                "eventTime": "2026-09-20T00:00:00Z",
                                "eventName": "StartInstances",
                                "eventSource": "ec2.amazonaws.com",
                                "awsRegion": "ap-northeast-2",
                                "sourceIPAddress": "AWS Internal",
                                "userIdentity": {"type": "AssumedRole", "arn": "AWS_ARN_REDACTED"},
                            }
                        ),
                    }
                ],
            )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(writer.indexes[0][0], "hospital-aws-cloud-events")
            self.assertEqual(writer.documents[0][2]["severity"], "HIGH")
            self.assertTrue((Path(directory) / "state.json").exists())
