import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticProjectTests(unittest.TestCase):
    def test_python_files_compile(self):
        for path in [
            ROOT / "docker/rootfs/opt/mailstack/setup/server.py",
            ROOT / "docker/rootfs/opt/mailstack/setup/apply_config.py",
            ROOT / "docker/rootfs/opt/mailstack/postfixadmin/patch_dns_page.py",
        ]:
            ast.parse(path.read_text(), filename=str(path))

    def test_compose_exposes_required_ports(self):
        compose = (ROOT / "compose.yaml").read_text()
        for port in ["25:25", "465:465", "587:587", "143:143", "993:993", "80:80", "443:443"]:
            self.assertIn(port, compose)
        self.assertIn("${MAILSTACK_SETUP_PORT:-8080}:8080", compose)

    def test_setup_ui_uses_tokenized_routes(self):
        server = (ROOT / "docker/rootfs/opt/mailstack/setup/server.py").read_text()
        self.assertIn("/setup/{token()}", server)
        self.assertIn("secrets.compare_digest", server)
        self.assertIn("Admin Password", server)
        self.assertIn("DNS Records", server)
        self.assertIn("Connection Details", server)
        self.assertIn("/connection", server)

    def test_setup_form_only_asks_for_public_mail_details(self):
        server = (ROOT / "docker/rootfs/opt/mailstack/setup/server.py").read_text()
        self.assertIn('name="mail_hostname"', server)
        self.assertIn('name="roundcube_domain"', server)
        self.assertIn('name="postfixadmin_domain"', server)
        self.assertIn('name="admin_email"', server)
        self.assertIn('name="admin_password"', server)
        self.assertNotIn('name="domain"', server)
        self.assertNotIn('name="public_ip"', server)
        self.assertNotIn('name="timezone"', server)
        self.assertNotIn("database password", server.lower())

    def test_postfixadmin_dns_patch_writes_dns_page(self):
        patch = (ROOT / "docker/rootfs/opt/mailstack/postfixadmin/patch_dns_page.py").read_text()
        self.assertIn("mailstack_dns.php", patch)
        self.assertIn("mailstack_connection.php", patch)
        self.assertIn("SMTP submission", patch)
        self.assertIn("STARTTLS", patch)
        self.assertIn("roundcube_domain", patch)
        self.assertIn("postfixadmin_domain", patch)
        self.assertIn("default._domainkey", patch)
        self.assertIn("v=DMARC1", patch)

    def test_database_passwords_are_generated_internally(self):
        apply_config = (ROOT / "docker/rootfs/opt/mailstack/setup/apply_config.py").read_text()
        self.assertIn("secrets.token_urlsafe(32)", apply_config)
        self.assertIn("postfixadmin_db_password", apply_config)
        self.assertIn("roundcube_db_password", apply_config)
        self.assertIn("php_crypt:BLOWFISH:12:{{BLF-CRYPT}}", apply_config)

    def test_host_script_does_not_generate_mail_conf(self):
        script = (ROOT / "mailstack.sh").read_text()
        self.assertNotIn("mail.conf", script)
        self.assertIn("MailStack setup URL", script)

    def test_host_script_can_install_docker(self):
        script = (ROOT / "mailstack.sh").read_text()
        self.assertIn("install_docker_linux()", script)
        self.assertIn("install_docker_macos()", script)
        self.assertIn("https://get.docker.com", script)
        self.assertIn("Docker Desktop", script)
        self.assertIn("ensure_project_files()", script)
        self.assertIn("MAILSTACK_REPO_URL", script)
        self.assertIn("choose_setup_port()", script)
        self.assertIn("port_is_available()", script)
        self.assertIn("running_setup_port()", script)
        self.assertIn("sync_setup_port_from_running_container", script)
        self.assertIn("compose port mail 8080", script)
        self.assertIn("MAILSTACK_PUBLIC_HOST", script)
        self.assertIn("MAILSTACK_SETUP_PORT_START", script)
        self.assertIn("MAILSTACK_SETUP_PORT_END", script)
        self.assertNotIn("ask_yes_no", script)
        self.assertNotIn("[y/N]", script)
        self.assertNotIn("MAILSTACK_ASSUME_YES", script)


if __name__ == "__main__":
    unittest.main()
