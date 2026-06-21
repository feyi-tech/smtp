import ast
import importlib.util
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

    def test_web_apps_support_https(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        apply_config = (ROOT / "docker/rootfs/opt/mailstack/setup/apply_config.py").read_text()
        server = (ROOT / "docker/rootfs/opt/mailstack/setup/server.py").read_text()
        patch = (ROOT / "docker/rootfs/opt/mailstack/postfixadmin/patch_dns_page.py").read_text()
        supervisor = (ROOT / "docker/rootfs/etc/supervisor/conf.d/mailstack.conf").read_text()

        self.assertIn("certbot", dockerfile)
        self.assertIn("mailstack-cert-renew", supervisor)
        self.assertIn("listen 443 ssl", apply_config)
        self.assertIn("ssl_certificate", apply_config)
        self.assertIn("try_letsencrypt", apply_config)
        self.assertIn("mailstack-selfsigned", apply_config)
        self.assertIn("https://{host}", apply_config)
        self.assertIn("https://{html.escape(webmail)}/", server)
        self.assertIn("'https://' . $roundcubeHost", patch)

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
        self.assertIn("v=spf1 ip4:", patch)
        self.assertIn("domain-select", patch)
        self.assertIn("data-copy", patch)
        self.assertIn("copy-status", patch)
        self.assertIn("/data/state/dkim/", patch)

    def test_database_passwords_are_generated_internally(self):
        apply_config = (ROOT / "docker/rootfs/opt/mailstack/setup/apply_config.py").read_text()
        self.assertIn("secrets.token_urlsafe(32)", apply_config)
        self.assertIn("postfixadmin_db_password", apply_config)
        self.assertIn("roundcube_db_password", apply_config)
        self.assertIn("roundcube_des_key", apply_config)
        self.assertIn("php_crypt:BLOWFISH:12:{{BLF-CRYPT}}", apply_config)

    def test_postfix_chroot_gets_dns_files(self):
        apply_config = (ROOT / "docker/rootfs/opt/mailstack/setup/apply_config.py").read_text()
        entrypoint = (ROOT / "docker/rootfs/usr/local/bin/mailstack-entrypoint").read_text()
        for content in [apply_config, entrypoint]:
            self.assertIn("/var/spool/postfix/etc", content)
            self.assertIn("resolv.conf", content)
            self.assertIn("nsswitch.conf", content)

    def test_roundcube_uses_secure_local_mail_connections(self):
        apply_config = (ROOT / "docker/rootfs/opt/mailstack/setup/apply_config.py").read_text()
        self.assertIn("$config['imap_host'] = 'ssl://127.0.0.1:993';", apply_config)
        self.assertIn("$config['smtp_host'] = 'tls://127.0.0.1:587';", apply_config)
        self.assertIn("$config['imap_conn_options']", apply_config)
        self.assertIn("$config['smtp_conn_options']", apply_config)
        self.assertIn("'allow_self_signed' => true", apply_config)

    def test_dkim_public_txt_is_readable_by_web_ui(self):
        dkim = (ROOT / "docker/rootfs/usr/local/bin/mailstack-dkim-domain").read_text()
        setup = (ROOT / "docker/rootfs/opt/mailstack/setup/server.py").read_text()
        apply_config = (ROOT / "docker/rootfs/opt/mailstack/setup/apply_config.py").read_text()
        patch = (ROOT / "docker/rootfs/opt/mailstack/postfixadmin/patch_dns_page.py").read_text()
        self.assertIn("/data/state/dkim", dkim)
        self.assertIn("cp \"$KEY_DIR/$SELECTOR.txt\"", dkim)
        self.assertIn("chmod 644 \"$PUBLIC_DIR/$DOMAIN.$SELECTOR.txt\"", dkim)
        self.assertIn("chmod 711 /data/state", dkim)
        self.assertIn("/data/state/dkim", setup)
        self.assertIn("preg_match_all", patch)
        self.assertIn("sync_dkim_domains", apply_config)

    def test_dkim_parser_concatenates_txt_chunks(self):
        path = ROOT / "docker/rootfs/opt/mailstack/setup/server.py"
        spec = importlib.util.spec_from_file_location("mailstack_setup_server", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        raw = 'default._domainkey IN TXT ( "v=DKIM1; h=sha256; k=rsa; " "p=abc" "def" ) ; comment'
        self.assertEqual(module.parse_dkim_txt(raw), "v=DKIM1; h=sha256; k=rsa; p=abcdef")

    def test_dns_pages_have_domain_selector_and_copy_controls(self):
        server = (ROOT / "docker/rootfs/opt/mailstack/setup/server.py").read_text()
        patch = (ROOT / "docker/rootfs/opt/mailstack/postfixadmin/patch_dns_page.py").read_text()
        for content in [server, patch]:
            self.assertIn("domain-select", content)
            self.assertIn("domain-panel", content)
            self.assertIn("data-copy", content)
            self.assertIn("copy-field", content)
            self.assertIn("copy-preview", content)
            self.assertIn("text-overflow: ellipsis", content)
            self.assertIn("table-layout: fixed", content)
            self.assertIn("Copied", content)
        self.assertIn("preview_value", server)
        self.assertIn("mailstack_preview", patch)

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

    def test_host_script_can_update_and_recreate(self):
        script = (ROOT / "mailstack.sh").read_text()
        apply_saved = (ROOT / "docker/rootfs/usr/local/bin/mailstack-apply-saved").read_text()
        self.assertIn('SCRIPT_ARGS=("$@")', script)
        self.assertIn("update_project_files()", script)
        self.assertIn("git pull --ff-only", script)
        self.assertIn("refresh_project_archive()", script)
        self.assertIn("restart_updated_script()", script)
        self.assertIn("exec \"$0\" \"${SCRIPT_ARGS[@]}\"", script)
        self.assertIn("reapply_saved_setup()", script)
        self.assertIn("mailstack-apply-saved", script)
        self.assertIn("compose up -d --build\n    reapply_saved_setup", script)
        self.assertIn("update)", script)
        self.assertIn("compose up -d --build --force-recreate", script)
        self.assertIn("subprocess.call", apply_saved)
        self.assertIn("/opt/mailstack/setup/apply_config.py", apply_saved)
        self.assertIn("install|up|update|url|status|logs|down|destroy --yes", script)


if __name__ == "__main__":
    unittest.main()
