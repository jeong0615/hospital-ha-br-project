import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ReprovisioningContractTest(unittest.TestCase):
    def read(self, relative_path):
        return (ROOT / relative_path).read_text()

    def test_ansible_runs_database_before_application_tiers(self):
        source = self.read("automation/ansible/site.yml")
        db = source.index("hosts: db_servers")
        emr = source.index("hosts: doc_servers")
        web = source.index("hosts: web_servers")
        self.assertLess(db, emr)
        self.assertLess(emr, web)

    def test_ansible_stops_later_tiers_after_a_play_failure(self):
        source = self.read("automation/ansible/site.yml")
        self.assertEqual(source.count("any_errors_fatal: true"), 4)

    def test_fresh_host_bootstraps_python_before_ansible_modules(self):
        source = self.read("automation/ansible/site.yml")
        self.assertIn("hosts: all", source)
        self.assertIn("gather_facts: false", source)
        self.assertIn("raw:", source)
        self.assertIn("apt-get install --yes python3 python3-apt", source)
        self.assertIn("python3 -c 'import apt'", source)
        self.assertLess(source.index("raw:"), source.index("hosts: db_servers"))

    def test_db_policy_flags_are_boolean_and_honor_existing_data(self):
        source = self.read("automation/ansible/roles/db_recovery/tasks/main.yml")
        for marker in (
            "(lookup('env', 'DB_ALLOW_EXISTING_DATA') | default('false', true) | lower) == 'true'",
            "(lookup('env', 'DB_NR_ENABLED') | default('false', true) | lower) == 'true'",
            "(lookup('env', 'DB_PROMOTE_ON_FAILOVER') | default('false', true) | lower) == 'true'",
            "db_backup_key | length > 0 or db_allow_existing_data",
            "when: db_nr_enabled",
            "when: db_promote_on_failover",
            "when: not db_promote_on_failover",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("db_allow_existing_data == 'true'", source)
        self.assertNotIn("db_nr_enabled == 'true'", source)
        self.assertNotIn("db_promote_on_failover == 'true'", source)
        self.assertNotIn("db_promote_on_failover != 'true'", source)

    def test_web_runtime_uses_immutable_ecr_docker_deployment(self):
        source = self.read("automation/ansible/roles/web_deploy/tasks/main.yml")
        for marker in (
            "docker.io",
            "aws ecr get-login-password",
            "docker pull",
            "--restart unless-stopped",
            "hospital-dr-web",
            "--publish 127.0.0.1:3000:3000",
            "docker inspect",
            "State.Health.Status",
            "nginx -t",
        ):
            self.assertIn(marker, source)
        self.assertIn("DR_IMAGE_TAG", source)
        self.assertIn("WEB_DB_PASSWORD", source)
        self.assertIn("SESSION_SECRET", source)
        self.assertIn("DR_TLS_CERT_FILE", source)
        self.assertIn("DR_TLS_KEY_FILE", source)
        self.assertIn("REQUIRE_PRODUCTION_TLS", source)
        self.assertIn("openssl x509", source)
        self.assertIn("systemctl disable --now pm2-ubuntu.service", source)
        self.assertNotIn("nodesource", source)
        self.assertNotIn("synchronize:", source)

    def test_ansible_reprovisioning_reports_real_config_changes(self):
        emr = self.read("automation/ansible/roles/doc_deploy/tasks/main.yml")
        db = self.read("automation/ansible/roles/db_recovery/tasks/main.yml")
        self.assertIn("'Enabling module' in emr_apache_modules.stdout", emr)
        self.assertIn("'Enabling site' in emr_apache_site.stdout", emr)
        self.assertIn("'Disabling site' in emr_default_site.stdout", emr)
        self.assertIn("register: db_bind_config", db)
        self.assertIn("register: db_promotion_overrides", db)
        self.assertIn("register: db_standby_config", db)
        self.assertIn("db_standby_config.changed", db)

    def test_apache_reload_is_conditional_and_proxy_is_health_checked(self):
        source = self.read("automation/ansible/roles/doc_deploy/tasks/main.yml")
        self.assertIn("register: emr_apache_config", source)
        self.assertIn("state: started", source)
        self.assertIn("state: reloaded", source)
        self.assertIn("emr_apache_config.changed", source)
        self.assertIn("emr_apache_health.status == 200", source)

    def test_emr_runtime_uses_immutable_ecr_docker_deployment(self):
        source = self.read("automation/ansible/roles/doc_deploy/tasks/main.yml")
        for marker in (
            "docker.io",
            "aws ecr get-login-password",
            "docker pull",
            "--restart unless-stopped",
            "hospital-dr-emr",
            "--publish 127.0.0.1:3000:3000",
            "docker inspect",
            "State.Health.Status",
            "apache2ctl configtest",
        ):
            self.assertIn(marker, source)
        self.assertIn("DR_IMAGE_TAG", source)
        self.assertIn("DOC_DB_PASSWORD", source)
        self.assertIn("/api/patients", source)
        self.assertIn("systemctl disable --now pm2-ubuntu.service", source)
        self.assertNotIn("nodesource", source)
        self.assertNotIn("synchronize:", source)

    def test_container_images_keep_health_contract(self):
        web_dockerfile = self.read("services/web/Dockerfile")
        emr_dockerfile = self.read("services/emr/Dockerfile")
        self.assertIn("HEALTHCHECK", web_dockerfile)
        self.assertIn("/health", web_dockerfile)
        self.assertIn("HEALTHCHECK", emr_dockerfile)
        self.assertIn("http://127.0.0.1:3000/", emr_dockerfile)

    def test_legacy_chatops_test_compose_is_retired(self):
        self.assertFalse((ROOT / "services/docker-compose.dr-test.yml").exists())


    def test_existing_container_metadata_avoids_incompatible_json_filter(self):
        for relative_path, field_marker in (
            (
                "automation/ansible/roles/web_deploy/tasks/main.yml",
                "web_container_image",
            ),
            (
                "automation/ansible/roles/doc_deploy/tasks/main.yml",
                "emr_container_image",
            ),
        ):
            source = self.read(relative_path)
            self.assertIn("docker inspect --format", source)
            self.assertIn(".Config.Image", source)
            self.assertIn(field_marker, source)
            self.assertNotIn("from_json", source)

    def test_container_running_comparison_handles_ansible_boolean_conversion(self):
        for relative_path, variable in (
            (
                "automation/ansible/roles/web_deploy/tasks/main.yml",
                "web_container_running",
            ),
            (
                "automation/ansible/roles/doc_deploy/tasks/main.yml",
                "emr_container_running",
            ),
        ):
            source = self.read(relative_path)
            self.assertIn(
                f"({variable} | string | lower) != 'true'",
                source,
            )

    def test_database_standby_contract_is_persistent_and_explicit(self):
        cnf = self.read(
            "automation/ansible/roles/db_recovery/files/z-hospital-dr-standby.cnf"
        )
        role = self.read("automation/ansible/roles/db_recovery/tasks/main.yml")
        for marker in (
            "server-id=20",
            "log_bin=mysql-bin",
            "binlog_format=ROW",
            "gtid_mode=OFF",
            "read_only=ON",
            "super_read_only=ON",
            "skip_replica_start=ON",
        ):
            self.assertIn(marker, cnf)
        self.assertIn("z-hospital-dr-standby.cnf", role)
        self.assertIn("/etc/mysql/debian.cnf", role)
        self.assertIn("SET GLOBAL super_read_only=OFF", role)
        self.assertIn("SET GLOBAL read_only=ON", role)
        self.assertIn("trap cleanup_mysql_defaults EXIT", role)
        self.assertIn("DB_ALLOW_EXISTING_DATA", role)
        self.assertIn("DB_BACKUP_ENCRYPTION_KEY", role)
        self.assertIn("db_backup_status_file", role)
        self.assertIn("Stage the selected matching replication coordinate", role)
        self.assertNotIn("hospital_schema.sql", role)
        self.assertNotIn("ignore_errors", role)


    def test_db_account_task_uses_bash_for_pipefail(self):
        source = self.read("automation/ansible/roles/db_recovery/tasks/main.yml")
        anchor = "- name: \"Ensure Web and EMR database access / Web 및 EMR DB 접근 권한 구성\""
        start = source.index(anchor)
        end = source.index("\n- name: ", start + len(anchor))
        task = source[start:end]
        self.assertIn("  args:\n    executable: /bin/bash\n", task)

    def test_backup_restore_requires_matching_status_pair(self):
        source = self.read(
            "automation/ansible/roles/db_recovery/files/db_restore.sh"
        )
        self.assertIn(".sql.gz.enc", source)
        self.assertIn(".master_status", source)
        self.assertIn("Matching master_status artifact is missing", source)
        self.assertIn("has no usable file/position pair", source)
        self.assertIn("status_file_name", source)
        replication = self.read(
            "automation/ansible/roles/db_recovery/files/db_replication_configure.sh"
        )
        self.assertIn("Position metadata is not a valid binlog file/position pair", replication)

    def test_db_role_fails_closed_on_partial_backup_input(self):
        source = self.read("automation/ansible/roles/db_recovery/tasks/main.yml")
        self.assertIn("Require an explicit and complete backup input pair", source)
        self.assertIn("db_backup_file_stat.stat.readable", source)
        self.assertIn("db_backup_status_file_stat.stat.readable", source)
        self.assertIn("db_target_name is match('^[A-Za-z0-9_]+$')", source)

    def test_provisioning_entrypoint_requires_a_complete_fresh_db_input(self):
        demo = self.read("infra/terraform/run_demo.sh")
        preflight = self.read("operations/runtime/provisioning_preflight.sh")
        for source in (demo, preflight):
            self.assertIn("DB_BACKUP_ENCRYPTION_KEY", source)
            self.assertIn("DB_BACKUP_FILE", source)
            self.assertIn("DB_ALLOW_EXISTING_DATA=true", source)
        self.assertIn("cannot be combined with backup restore inputs", demo)

    def test_provisioning_entrypoint_resolves_repository_runtime_paths(self):
        demo = self.read("infra/terraform/run_demo.sh")
        self.assertIn('source "$SCRIPT_DIR/../../operations/runtime_paths.sh"', demo)
        self.assertNotIn('source "$SCRIPT_DIR/../operations/runtime_paths.sh"', demo)

    def test_provisioning_keeps_database_in_standby_mode(self):
        demo = self.read("infra/terraform/run_demo.sh")
        environment = self.read("operations/finops/dr_environment_control.sh")
        self.assertIn('DR_PROVISION_ONLY:-false', demo)
        self.assertIn('DB_PROMOTE_ON_FAILOVER=false', demo)
        self.assertIn('DR_PROVISION_ONLY=true "$TF_DIR/run_demo.sh"', environment)

    def test_full_provisioning_keeps_nat_mutation_in_operations_nat(self):
        demo = self.read("infra/terraform/run_demo.sh")
        self.assertIn('NAT_ENABLE_SCRIPT="${NAT_ENABLE_SCRIPT:-$DR_NAT_DIR/nat_enable.sh}"', demo)
        self.assertIn('NAT_STATUS_SCRIPT="${NAT_STATUS_SCRIPT:-$DR_NAT_DIR/nat_status.sh}"', demo)
        self.assertIn('NAT_TERRAFORM_ENABLE=false', demo)
        self.assertIn('"$NAT_STATUS_SCRIPT"', demo)
        self.assertIn('"$NAT_ENABLE_SCRIPT"', demo)
        self.assertIn('-var="enable_nat_gateway=$NAT_TERRAFORM_ENABLE"', demo)
        self.assertNotIn('-var="enable_nat_gateway=true"', demo)

    def test_runtime_image_and_ecr_contract(self):
        demo = self.read("infra/terraform/run_demo.sh")
        for marker in (
            "DR_IMAGE_TAG",
            "git -C",
            "ECR_REGION",
            "ECR_WEB_REPOSITORY",
            "ECR_EMR_REPOSITORY",
            "cloudflare_zone_id=$CF_ZONE_ID",
            "DB_ALLOW_EXISTING_DATA",
        ):
            self.assertIn(marker, demo)
        self.assertIn('DR_IMAGE_TAG" =~ ^[0-9a-fA-F]{40}$', demo)
        self.assertNotIn("DR_IMAGE_TAG:-latest", demo)

    def test_legacy_replication_defaults_match_direct_nr_contract(self):
        target = self.read(
            "automation/ansible/roles/db_recovery/files/db_replication_configure.sh"
        )
        source = self.read("operations/db/db_nr_source_setup.sh")
        self.assertIn('DB_REPL_USER:-repl1', target)
        self.assertIn('DB_REPL_MODE:-position', target)
        self.assertIn('DB_REPL_USER:-repl1', source)
        self.assertIn('DB_SOURCE_SERVER_ID:-1', source)
        self.assertIn('DB_REPL_ALLOWED_HOST:-15.165.140.84', source)
        self.assertNotIn("'repl' IDENTIFIED", source)
        env_example = self.read("operations/chatops/.env.example")
        self.assertIn("DB_REPL_USER=repl1", env_example)

    def test_lifecycle_start_uses_docker_not_pm2(self):
        source = self.read("operations/finops/dr_environment_control.sh")
        self.assertNotIn("pm2-ubuntu", source)
        self.assertIn("hospital-dr-web", source)
        self.assertIn("hospital-dr-emr", source)
        self.assertIn("State.Health.Status", source)

    def test_cloudflare_zone_is_external_input_and_runtime_mutations_are_ignored(self):
        terraform = self.read("infra/terraform/main.tf")
        demo = self.read("infra/terraform/run_demo.sh")
        self.assertIn('variable "cloudflare_zone_id"', terraform)
        self.assertEqual(terraform.count("zone_id = var.cloudflare_zone_id"), 2)
        self.assertNotIn("1b91ec95d7d8131b5ce5c8ed0317c451", terraform)
        self.assertIn("cloudflare_zone_id=$CF_ZONE_ID", demo)
        self.assertNotIn('patch_cloudflare_zone_setting "ssl"', demo)
        self.assertNotIn('patch_cloudflare_zone_setting "always_use_https"', demo)
        self.assertEqual(terraform.count("prevent_destroy = true"), 2)
        for resource_name in ("hospital_web", "hospital_emr"):
            start = terraform.index(f'resource "cloudflare_record" "{resource_name}"')
            next_resource = terraform.find("\nresource ", start + 1)
            block = terraform[start:] if next_resource == -1 else terraform[start:next_resource]
            self.assertIn("ignore_changes = [type, content, ttl, proxied]", block)

    def test_db_replication_check_has_no_controller_path_dependency(self):
        source = self.read(
            "automation/ansible/roles/db_recovery/files/db_replication_check.sh"
        )
        self.assertNotIn("/opt/hospital-dr", source)
        self.assertIn("DR_ZBX_SCRIPT", source)

    def test_operational_helpers_use_repository_runtime_paths(self):
        for relative_path in (
            "operations/db/db_backup.sh",
            "operations/finops/dr_full_status.sh",
            "operations/nat/nat_enable.sh",
            "operations/nat/nat_status.sh",
            "operations/nat/nat_disable.sh",
        ):
            source = self.read(relative_path)
            if relative_path.startswith("operations/nat/"):
                source += self.read("operations/nat/nat_common.sh")
            self.assertIn("runtime_paths.sh", source)
            self.assertNotIn("/opt/hospital-dr", source)

    def test_fixed_nat_eip_is_checked_when_provided(self):
        demo = self.read("infra/terraform/run_demo.sh")
        self.assertIn("NAT_EIP_PUBLIC_IP", demo)
        self.assertIn("nat_eip_public_ip", demo)
        source = self.read("operations/db/db_nr_source_setup.sh")
        self.assertIn("15.165.140.84", source)

    def test_emr_auth_has_no_hardcoded_test_login_bypass(self):
        source = self.read("services/emr/backend/auth.js")
        self.assertNotIn("testUsers", source)
        self.assertNotIn("password === 'pass'", source)
        self.assertNotIn('password === "pass"', source)

    def test_emr_database_requires_explicit_runtime_configuration(self):
        source = self.read("services/emr/backend/db.js")
        self.assertIn("requiredEnvironment", source)
        self.assertIn("DB_HOST", source)
        self.assertIn("DB_USER", source)
        self.assertIn("DB_PASSWORD", source)
        self.assertIn("DB_NAME", source)
        self.assertNotIn("192.168.100.235", source)
        self.assertNotIn("process.env.DB_USER || 'admin'", source)

    def test_web_session_requires_external_secret_instead_of_default(self):
        source = self.read("services/web/app.js")
        self.assertIn("SESSION_SECRET must be supplied", source)
        self.assertIn("secret: sessionSecret", source)
        self.assertNotIn("change-this-secret", source)

    def test_unused_zabbix_check_module_does_not_expose_api_token(self):
        source = self.read("services/web/routes/check.js")
        self.assertIn("apiinfo.version", source)
        self.assertNotIn("user.login", source)
        self.assertNotIn("ZABBIX_PASSWORD", source)
        self.assertNotIn("token:", source)

    def test_slack_interactions_have_signature_replay_protection(self):
        source = self.read("operations/chatops/chatops_core.py")
        for marker in (
            "SLACK_SIGNATURE_MAX_AGE_SECONDS",
            "remember_slack_request",
            "slack_replay_cache",
            "slack_replay_lock",
            '"error": "replayed Slack request"',
        ):
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
