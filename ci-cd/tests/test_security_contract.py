import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SECURITY_DIR = ROOT / "operations" / "security"
sys.path.insert(0, str(SECURITY_DIR))

from build_security_summary import build_summary
from finalize_security_summary import finalize
from wazuh_chatops_integration import forward
from wazuh_pipeline_check import check_pipeline


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self):
        return self._payload


class SecurityContractTest(unittest.TestCase):
    def test_devsecops_summary_counts_findings_without_raw_report_content(self):
        with tempfile.TemporaryDirectory() as directory:
            os_report = Path(directory) / "os.json"
            library_report = Path(directory) / "library.json"
            os_report.write_text(
                json.dumps(
                    {
                        "Results": [
                            {
                                "Target": "ubuntu",
                                "Vulnerabilities": [
                                    {
                                        "Severity": "HIGH",
                                        "VulnerabilityID": "CVE-TEST-1",
                                        "PkgName": "openssl",
                                        "InstalledVersion": "1.0",
                                        "FixedVersion": "2.0",
                                        "Status": "fixed",
                                    },
                                    {
                                        "Severity": "CRITICAL",
                                        "VulnerabilityID": "CVE-TEST-2",
                                        "PkgName": "zlib",
                                        "InstalledVersion": "1.1",
                                        "FixedVersion": "",
                                        "Status": "will_not_fix",
                                    },
                                ],
                            }
                        ]
                    }
                )
            )
            library_report.write_text(json.dumps({"Results": []}))
            summary = build_summary(
                SimpleNamespace(
                    os_report=os_report,
                    library_report=library_report,
                    build="success",
                    health="success",
                    npm_exit="0",
                    repository="org/repo",
                    run_id="123",
                    commit_sha="a" * 40,
                    image="web:sha",
                    ecr_repository="hospital-dr-web",
                    ecr_push="STAGED",
                    ecr_image_digest="",
                )
            )
            self.assertEqual(summary["security_gate"], "FAIL")
            self.assertEqual(summary["trivy"]["os"]["fixable"], 1)
            self.assertEqual(summary["trivy"]["os"]["unique_cves"], ["CVE-TEST-1", "CVE-TEST-2"])
            self.assertEqual(summary["trivy"]["os"]["findings"][0]["classification"], "FIX_AVAILABLE")
            self.assertEqual(summary["trivy"]["os"]["findings"][1]["classification"], "NO_FIXED_VERSION")
            self.assertEqual(summary["ecr_push"], "STAGED")
            self.assertNotIn("FixedVersion", json.dumps(summary))

            source = Path(directory) / "summary.json"
            target = Path(directory) / "final.json"
            source.write_text(json.dumps(summary))
            finalize(source, target, "PASS", "sha256:" + "b" * 64)
            final = json.loads(target.read_text())
            self.assertEqual(final["ecr_push"], "PASS")
            self.assertEqual(final["ecr_image_digest"], "sha256:" + "b" * 64)

    @patch("wazuh_chatops_integration.requests.post")
    def test_wazuh_integration_forwards_json_without_printing_it(self, post):
        post.return_value = FakeResponse({}, 204)
        with tempfile.TemporaryDirectory() as directory:
            alert_file = Path(directory) / "alert.json"
            alert_file.write_text(json.dumps({"rule": {"id": "5710"}, "agent": {"name": "test"}}))
            self.assertEqual(forward(str(alert_file), "http://127.0.0.1:5000/wazuh/alert"), 204)
            self.assertEqual(post.call_args.kwargs["json"]["rule"]["id"], "5710")

    @patch("wazuh_pipeline_check.requests.post")
    @patch("wazuh_pipeline_check.requests.get")
    def test_wazuh_pipeline_checker_reports_indexer_evidence(self, get, post):
        get.return_value = FakeResponse({"status": "green"})
        post.return_value = FakeResponse(
            {
                "hits": {"total": {"value": 2}},
                "aggregations": {
                    "agents": {"buckets": [{"key": "hospital-web", "doc_count": 2}]},
                    "rules": {"buckets": [{"key": "5710", "doc_count": 1}, {"key": "5502", "doc_count": 1}]},
                    "groups": {"buckets": [{"key": "authentication_failed", "doc_count": 1}]},
                },
            }
        )
        result = check_pipeline("https://indexer", "reader", "not-printed", ca_cert=False, lookback=60)
        self.assertEqual(result["indexer"], "PASS")
        self.assertEqual(result["ingestion"], "PASS")
        self.assertEqual(result["document_count"], 2)
        self.assertTrue(result["capabilities"]["authentication"])
        self.assertTrue(result["capabilities"]["fim"])
        self.assertEqual(result["agents"]["hospital-web"], 2)


if __name__ == "__main__":
    unittest.main()
