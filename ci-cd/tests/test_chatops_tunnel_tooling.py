import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "operations" / "chatops" / "systemd"
INSTALLER = SYSTEMD / "install_chatops_tunnel.sh"
CHECKER = SYSTEMD / "check_fixed_chatops_endpoint.sh"
UNIT = SYSTEMD / "hospital-dr-chatops-tunnel.service"
README = SYSTEMD / "README.md"


class ChatOpsTunnelToolingContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = INSTALLER.read_text(encoding="utf-8")
        cls.checker = CHECKER.read_text(encoding="utf-8")
        cls.unit = UNIT.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")

    def test_installer_is_fail_closed_and_explicit(self):
        for token in (
            "--dry-run",
            "--apply",
            "--enable",
            "--start",
            "BLOCKED_BY_CLOUDFLARE_TOKEN",
            "BLOCKED_BY_CLOUDFLARED_BINARY",
            "--dry-run and --apply cannot be combined",
            "NO_FILE_CHANGE=YES",
        ):
            self.assertIn(token, self.installer)
        self.assertIn('TOKEN_FILE="/etc/cloudflared/chatops-tunnel.token"', self.installer)
        self.assertIn('CLOUDFLARED_BIN="/usr/bin/cloudflared"', self.installer)
        self.assertIn("systemctl daemon-reload", self.installer)

    def test_installer_does_not_touch_ngrok_or_print_secret(self):
        self.assertIn("NGROK_MUTATION=NONE", self.installer)
        self.assertNotIn('cat "$TOKEN_FILE"', self.installer)
        self.assertNotIn('echo "$CLOUDFLARE_API_TOKEN"', self.installer)
        self.assertNotIn('--token-file "$TOKEN_FILE"', self.installer)

    def test_unit_keeps_existing_connector_contract(self):
        self.assertIn("User=cloud", self.unit)
        self.assertIn("LoadCredential=chatops-tunnel.token:/etc/cloudflared/chatops-tunnel.token", self.unit)
        self.assertIn("/usr/bin/cloudflared tunnel --no-autoupdate run --token-file", self.unit)
        self.assertIn("Requires=hospital-dr-chatops.service", self.unit)

    def test_checker_is_fixed_host_read_only_and_accepts_post_only_4xx(self):
        self.assertIn('BASE_URL="https://chatops.hospital-web.pp.ua"', self.checker)
        for path in ("/slack/failover/action", "/slack/failback/action", "/slack/action"):
            self.assertIn(path, self.checker)
        self.assertIn("[2-4][0-9][0-9]", self.checker)
        self.assertIn("ssl_verify_result", self.checker)
        self.assertNotIn("-k", self.checker)
        self.assertNotIn("systemctl", self.checker)
        self.assertNotIn("ngrok stop", self.checker)

    def test_readme_preserves_ngrok_until_fixed_routes_pass(self):
        self.assertIn("Keep the working ngrok URL", self.readme)
        self.assertIn("chatops.hospital-web.pp.ua -> http://127.0.0.1:5000", self.readme)
        self.assertIn("chatops.hospital-web.pp.ua/slack/failover/action", self.readme)
        self.assertIn("chatops.hospital-web.pp.ua/slack/failback/action", self.readme)
        self.assertIn("chatops.hospital-web.pp.ua/slack/action", self.readme)


if __name__ == "__main__":
    unittest.main()
