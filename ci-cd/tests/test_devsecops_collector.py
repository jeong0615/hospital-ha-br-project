import json
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from pathlib import Path


SECURITY_DIR = Path(__file__).resolve().parents[1] / "operations" / "security"
CI_DIR = Path(__file__).resolve().parents[1] / "operations" / "ci"
sys.path.insert(0, str(SECURITY_DIR))
sys.path.insert(0, str(CI_DIR))

from cleanup_github_artifacts import select_artifacts
from devsecops_indexer_collector import build_devsecops_document, extract_security_summaries
from wazuh_vulnerability_status import check_status


class DevSecOpsCollectorTest(unittest.TestCase):
    def test_extracts_secret_free_ci_summary_and_digest(self):
        run = {
            "id": 123,
            "run_number": 76,
            "head_sha": "a" * 40,
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
            "updated_at": "2026-09-20T05:19:48Z",
            "html_url": "https://github.com/example/run/123",
            "name": "Quality Gate and Docker Security Scan",
        }
        jobs = [
            {"name": "Python unittest and workflow YAML", "conclusion": "success"},
            {"name": "Secret scan", "conclusion": "success"},
            {"name": "web build and scan", "conclusion": "success"},
            {"name": "emr build and scan", "conclusion": "success"},
            {"name": "Terraform format and validate", "conclusion": "success"},
            {"name": "Ansible playbook syntax", "conclusion": "success"},
            {"name": "Push gated web image to ECR", "conclusion": "success"},
            {"name": "Push gated emr image to ECR", "conclusion": "success"},
        ]
        web_summary = {
            "trivy": {"os": {"critical": 4, "high": 52}, "library": {"critical": 0, "high": 0}},
            "ecr_image_digest": "",
        }
        emr_summary = {
            "trivy": {"os": {"critical": 4, "high": 52}, "library": {"critical": 0, "high": 0}},
            "ecr_image_digest": "",
        }
        log = "\n".join(
            [
                "web build and scan\tEmit structured summary to run log\tDEVSECOPS_SUMMARY_JSON_BEGIN",
                "web build and scan\tEmit structured summary to run log\t" + json.dumps(web_summary),
                "web build and scan\tEmit structured summary to run log\tDEVSECOPS_SUMMARY_JSON_END",
                "emr build and scan\tEmit structured summary to run log\tDEVSECOPS_SUMMARY_JSON_BEGIN",
                "emr build and scan\tEmit structured summary to run log\t" + json.dumps(emr_summary),
                "emr build and scan\tEmit structured summary to run log\tDEVSECOPS_SUMMARY_JSON_END",
                "Push gated web image to ECR\tPush immutable commit image\tdigest: sha256:" + "b" * 64,
                "Push gated emr image to ECR\tPush immutable commit image\tdigest: sha256:" + "c" * 64,
            ]
        )
        self.assertEqual(set(extract_security_summaries(log)), {"web", "emr"})
        document = build_devsecops_document(run, jobs, log, "jeong0615/hospital-ci-cd")
        self.assertEqual(document["conclusion"], "success")
        self.assertEqual(document["trivy_high"], 104)
        self.assertEqual(document["trivy_critical"], 8)
        self.assertEqual(document["ecr_push"], "PASS")
        self.assertEqual(document["web_image_digest"], "sha256:" + "b" * 64)
        self.assertNotIn("password", json.dumps(document).lower())

    def test_cleanup_only_selects_expired_or_old_ci_artifacts(self):
        artifacts = [
            {"id": 1, "name": "gated-image-web-old", "expired": True, "size_in_bytes": 100, "workflow_run": {"id": 1}},
            {"id": 2, "name": "docker-security-web-old", "expired": False, "size_in_bytes": 200, "workflow_run": {"id": 1}},
            {"id": 3, "name": "gitleaks-results.sarif", "expired": False, "size_in_bytes": 300, "workflow_run": {"id": 1}},
            {"id": 4, "name": "gated-image-web-new", "expired": False, "size_in_bytes": 400, "workflow_run": {"id": 2}},
        ]
        runs = [
            {"id": 2, "status": "completed", "updated_at": "2026-09-20T00:00:00Z"},
            {"id": 1, "status": "completed", "updated_at": "2026-09-19T00:00:00Z"},
        ]
        selected = select_artifacts(artifacts, runs, keep_runs=1, delete_expired=True, prune_superseded=True)
        self.assertEqual({item[0]["id"] for item in selected}, {1, 2})

    def test_vulnerability_status_distinguishes_indexed_data_from_agent_state(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "hits": {"total": {"value": 12, "relation": "eq"}},
            "aggregations": {"agents": {"buckets": [{"key": "hospital-web", "doc_count": 12}]}},
        }
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as config:
            config.write("<vulnerability-detection>\n<enabled>yes</enabled>\n</vulnerability-detection>\n")
            config.flush()
            with patch("wazuh_vulnerability_status.requests.post", return_value=response):
                result = check_status(
                    "https://indexer.example",
                    "reader",
                    "runtime-value",
                    config_path=config.name,
                    agent_status="disconnected",
                )
        self.assertEqual(result["pipeline"], "PASS")
        self.assertEqual(result["live_data"], "INDEXED")
        self.assertEqual(result["document_count"], 12)

    def test_vulnerability_status_marks_empty_index_blocked_when_agents_disconnected(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"hits": {"total": {"value": 0, "relation": "eq"}}}
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as config:
            config.write("<vulnerability-detection><enabled>yes</enabled></vulnerability-detection>")
            config.flush()
            with patch("wazuh_vulnerability_status.requests.post", return_value=response):
                result = check_status(
                    "https://indexer.example",
                    "reader",
                    "runtime-value",
                    config_path=config.name,
                    agent_status="disconnected",
                )
        self.assertEqual(result["pipeline"], "PASS")
        self.assertEqual(result["live_data"], "BLOCKED_BY_AGENT_CONNECTIVITY")


if __name__ == "__main__":
    unittest.main()
