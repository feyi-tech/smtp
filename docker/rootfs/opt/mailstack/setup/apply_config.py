#!/usr/bin/env python3
import argparse
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

STATE_DIR = Path("/data/state")
DB_PATH = STATE_DIR / "mailstack.db"
SECRETS_PATH = STATE_DIR / "secrets.json"
ADMIN_PASSWORD_PATH = STATE_DIR / "admin-password.json"
ROUNDCUBE_VERSION = os.environ.get("ROUNDCUBE_VERSION", "1.6.6")
POSTFIXADMIN_VERSION = os.environ.get("POSTFIXADMIN_VERSION", "3.3.13")
SELF_SIGNED_CERT = Path("/etc/ssl/mailstack/mailstack-selfsigned.crt")
SELF_SIGNED_KEY = Path("/etc/ssl/mailstack/mailstack-selfsigned.key")
LE_CERT = Path("/etc/letsencrypt/live/mailstack-web/fullchain.pem")
LE_KEY = Path("/etc/letsencrypt/live/mailstack-web/privkey.pem")


def run(args, check=True, input_text=None, env=None, display: str | None = None):
    printable = display or " ".join(args)
    print(f"+ {printable}", flush=True)
    proc = subprocess.run(args, text=True, input=input_text, capture_output=True, env=env)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="")
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed: {printable}")
    return proc


def read_settings() -> dict[str, str]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


def read_admin_password() -> str:
    data = json.loads(ADMIN_PASSWORD_PATH.read_text(encoding="utf-8"))
    password = data.get("admin_password", "")
    if not password:
        raise RuntimeError("Admin password was not provided.")
    return password


def read_secrets() -> dict[str, str]:
    if SECRETS_PATH.exists():
        data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
        changed = False
        if not data.get("roundcube_des_key"):
            data["roundcube_des_key"] = secrets.token_urlsafe(24)[:24]
            changed = True
        if changed:
            SECRETS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
            SECRETS_PATH.chmod(0o600)
        return data
    data = {
        "postfixadmin_db_password": secrets.token_urlsafe(32),
        "roundcube_db_password": secrets.token_urlsafe(32),
        "roundcube_des_key": secrets.token_urlsafe(24)[:24],
    }
    SECRETS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    SECRETS_PATH.chmod(0o600)
    return data


def sql_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def php_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def mail_domain(settings: dict[str, str]) -> str:
    host = settings["mail_hostname"].strip().lower().rstrip(".")
    if "." in host:
        return host.split(".", 1)[1]
    admin_email = settings.get("admin_email", "")
    if "@" in admin_email:
        return admin_email.rsplit("@", 1)[1].lower()
    return host or "localdomain"


def webmail_host(settings: dict[str, str]) -> str:
    return settings.get("roundcube_domain") or settings.get("mail_hostname") or "mail.example.com"


def admin_host(settings: dict[str, str]) -> str:
    return settings.get("postfixadmin_domain") or settings.get("mail_hostname") or "postfix.example.com"


def postfixadmin_url(settings: dict[str, str]) -> str:
    host = admin_host(settings)
    if host in {webmail_host(settings), settings.get("mail_hostname", "")}:
        return f"https://{host}/postfixadmin/"
    return f"https://{host}/"


def server_names(*hosts: str) -> str:
    names = []
    seen = set()
    for host in hosts:
        clean = host.strip().lower().rstrip(".")
        if clean and clean not in seen:
            names.append(clean)
            seen.add(clean)
    return " ".join(names) or "_"


def web_certificate_hosts(settings: dict[str, str]) -> list[str]:
    return [host for host in server_names(settings["mail_hostname"], webmail_host(settings), admin_host(settings)).split() if host != "_"]


def ensure_self_signed_cert(settings: dict[str, str]) -> tuple[Path, Path]:
    if SELF_SIGNED_CERT.exists() and SELF_SIGNED_KEY.exists():
        return SELF_SIGNED_CERT, SELF_SIGNED_KEY

    SELF_SIGNED_CERT.parent.mkdir(parents=True, exist_ok=True)
    hosts = web_certificate_hosts(settings)
    primary = hosts[0]
    san = ",".join(f"DNS:{host}" for host in hosts)
    run(
        [
            "openssl",
            "req",
            "-x509",
            "-nodes",
            "-newkey",
            "rsa:2048",
            "-days",
            "3650",
            "-keyout",
            str(SELF_SIGNED_KEY),
            "-out",
            str(SELF_SIGNED_CERT),
            "-subj",
            f"/CN={primary}",
            "-addext",
            f"subjectAltName={san}",
        ]
    )
    SELF_SIGNED_KEY.chmod(0o600)
    return SELF_SIGNED_CERT, SELF_SIGNED_KEY


def active_cert_paths() -> tuple[Path, Path]:
    if LE_CERT.exists() and LE_KEY.exists():
        return LE_CERT, LE_KEY
    return SELF_SIGNED_CERT, SELF_SIGNED_KEY


def reload_nginx() -> None:
    run(["nginx", "-t"])
    proc = run(["supervisorctl", "restart", "nginx"], check=False)
    if proc.returncode != 0:
        run(["nginx", "-s", "reload"], check=False)


def try_letsencrypt(settings: dict[str, str]) -> bool:
    if not shutil.which("certbot"):
        print("certbot is not installed; HTTPS will use the self-signed fallback certificate.")
        return False

    hosts = web_certificate_hosts(settings)
    args = [
        "certbot",
        "certonly",
        "--webroot",
        "-w",
        "/var/www/certbot",
        "--non-interactive",
        "--agree-tos",
        "--email",
        settings["admin_email"],
        "--cert-name",
        "mailstack-web",
        "--expand",
        "--keep-until-expiring",
    ]
    for host in hosts:
        args.extend(["-d", host])

    proc = run(args, check=False)
    if proc.returncode == 0 and LE_CERT.exists() and LE_KEY.exists():
        print("Let's Encrypt certificate is installed for the web apps.")
        return True

    print("Let's Encrypt certificate was not issued; HTTPS will use the self-signed fallback certificate.")
    return False


def wait_mysql() -> None:
    for _ in range(60):
        proc = run(["mysqladmin", "ping", "-uroot", "--silent"], check=False)
        if proc.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("MariaDB did not become ready.")


def mysql(sql: str, database: str | None = None) -> None:
    args = ["mysql", "-uroot"]
    if database:
        args.append(database)
    run(args, input_text=sql)


def ensure_database(settings: dict[str, str], sec: dict[str, str]) -> None:
    wait_mysql()
    mysql(
        f"""
CREATE DATABASE IF NOT EXISTS postfixadmin CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS roundcube CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'postfixadmin'@'localhost' IDENTIFIED BY {sql_quote(sec['postfixadmin_db_password'])};
CREATE USER IF NOT EXISTS 'roundcube'@'localhost' IDENTIFIED BY {sql_quote(sec['roundcube_db_password'])};
ALTER USER 'postfixadmin'@'localhost' IDENTIFIED BY {sql_quote(sec['postfixadmin_db_password'])};
ALTER USER 'roundcube'@'localhost' IDENTIFIED BY {sql_quote(sec['roundcube_db_password'])};
GRANT ALL PRIVILEGES ON postfixadmin.* TO 'postfixadmin'@'localhost';
GRANT ALL PRIVILEGES ON roundcube.* TO 'roundcube'@'localhost';
FLUSH PRIVILEGES;
"""
    )


def write_file(path: str | Path, content: str, mode: int = 0o644) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    p.chmod(mode)


def configure_postfix(settings: dict[str, str], sec: dict[str, str]) -> None:
    domain = mail_domain(settings)
    host = settings["mail_hostname"]
    dbpass = sec["postfixadmin_db_password"]

    map_common = f"""user = postfixadmin
password = {dbpass}
hosts = 127.0.0.1
dbname = postfixadmin
"""
    write_file("/etc/postfix/sql-domains.cf", map_common + "query = SELECT domain FROM domain WHERE domain='%s' AND active='1'\n", 0o640)
    write_file("/etc/postfix/sql-accounts.cf", map_common + "query = SELECT CONCAT('/var/vmail/', maildir) FROM mailbox WHERE username='%s' AND active='1'\n", 0o640)
    write_file("/etc/postfix/sql-aliases.cf", map_common + "query = SELECT goto FROM alias WHERE address='%s' AND active='1'\n", 0o640)

    for path in ["/etc/postfix/sql-domains.cf", "/etc/postfix/sql-accounts.cf", "/etc/postfix/sql-aliases.cf"]:
        shutil.chown(path, user="root", group="postfix")

    postconf = {
        "myhostname": host,
        "mydomain": domain,
        "myorigin": "$mydomain",
        "maillog_file": "/var/log/mail.log",
        "inet_interfaces": "all",
        "inet_protocols": "all",
        "mydestination": "$myhostname, localhost.$mydomain, localhost",
        "virtual_mailbox_domains": "proxy:mysql:/etc/postfix/sql-domains.cf",
        "virtual_mailbox_maps": "proxy:mysql:/etc/postfix/sql-accounts.cf",
        "virtual_alias_maps": "proxy:mysql:/etc/postfix/sql-aliases.cf",
        "virtual_mailbox_base": "/var/vmail",
        "virtual_uid_maps": "static:5000",
        "virtual_gid_maps": "static:5000",
        "virtual_transport": "lmtp:unix:private/dovecot-lmtp",
        "smtpd_sasl_type": "dovecot",
        "smtpd_sasl_path": "private/auth",
        "smtpd_sasl_auth_enable": "yes",
        "smtpd_recipient_restrictions": "permit_sasl_authenticated,permit_mynetworks,reject_unauth_destination",
        "smtpd_tls_security_level": "may",
        "smtpd_tls_cert_file": "/etc/ssl/certs/ssl-cert-snakeoil.pem",
        "smtpd_tls_key_file": "/etc/ssl/private/ssl-cert-snakeoil.key",
        "smtpd_milters": "inet:127.0.0.1:8891",
        "non_smtpd_milters": "inet:127.0.0.1:8891",
        "milter_default_action": "accept",
    }
    for key, value in postconf.items():
        run(["postconf", "-e", f"{key} = {value}"])

    master_services = [
        ("submission/inet", "submission inet n - y - - smtpd"),
        ("submission/inet/syslog_name", "postfix/submission"),
        ("submission/inet/smtpd_tls_security_level", "may"),
        ("submission/inet/smtpd_sasl_auth_enable", "yes"),
        ("submission/inet/smtpd_sasl_type", "dovecot"),
        ("submission/inet/smtpd_sasl_path", "private/auth"),
        ("submission/inet/smtpd_recipient_restrictions", "permit_sasl_authenticated,reject"),
        ("submission/inet/milter_macro_daemon_name", "ORIGINATING"),
        ("smtps/inet", "smtps inet n - y - - smtpd"),
        ("smtps/inet/syslog_name", "postfix/smtps"),
        ("smtps/inet/smtpd_tls_wrappermode", "yes"),
        ("smtps/inet/smtpd_sasl_auth_enable", "yes"),
        ("smtps/inet/smtpd_sasl_type", "dovecot"),
        ("smtps/inet/smtpd_sasl_path", "private/auth"),
        ("smtps/inet/smtpd_recipient_restrictions", "permit_sasl_authenticated,reject"),
        ("smtps/inet/milter_macro_daemon_name", "ORIGINATING"),
    ]
    for key, value in master_services:
        flag = "-M" if key.count("/") == 1 else "-P"
        run(["postconf", flag, f"{key}={value}"])


def configure_dovecot(sec: dict[str, str]) -> None:
    write_file(
        "/etc/dovecot/dovecot-sql.conf.ext",
        f"""driver = mysql
connect = host=127.0.0.1 dbname=postfixadmin user=postfixadmin password={sec['postfixadmin_db_password']}
default_pass_scheme = BLF-CRYPT
password_query = SELECT username AS user, password FROM mailbox WHERE username='%u' AND active='1'
user_query = SELECT '/var/vmail/%d/%n' AS home, 5000 AS uid, 5000 AS gid FROM mailbox WHERE username='%u' AND active='1'
""",
        0o640,
    )
    shutil.chown("/etc/dovecot/dovecot-sql.conf.ext", user="root", group="dovecot")
    write_file(
        "/etc/dovecot/conf.d/99-mailstack.conf",
        """protocols = imap lmtp
disable_plaintext_auth = yes
auth_mechanisms = plain login
mail_location = maildir:/var/vmail/%d/%n/Maildir
first_valid_uid = 5000
last_valid_uid = 5000
ssl = yes
ssl_cert = </etc/ssl/certs/ssl-cert-snakeoil.pem
ssl_key = </etc/ssl/private/ssl-cert-snakeoil.key

passdb {
  driver = sql
  args = /etc/dovecot/dovecot-sql.conf.ext
}

userdb {
  driver = sql
  args = /etc/dovecot/dovecot-sql.conf.ext
}

service lmtp {
  unix_listener /var/spool/postfix/private/dovecot-lmtp {
    mode = 0600
    user = postfix
    group = postfix
  }
}

service auth {
  unix_listener /var/spool/postfix/private/auth {
    mode = 0660
    user = postfix
    group = postfix
  }
}
""",
    )


def download(url: str, target: Path) -> None:
    if target.exists():
        return
    run(["wget", "-q", "-O", str(target), url])


def install_roundcube(sec: dict[str, str]) -> None:
    dest = Path("/var/www/roundcube")
    if not (dest / "index.php").exists() and not (dest / "public_html/index.php").exists():
        archive = Path(f"/tmp/roundcube-{ROUNDCUBE_VERSION}.tar.gz")
        download(
            f"https://github.com/roundcube/roundcubemail/releases/download/{ROUNDCUBE_VERSION}/roundcubemail-{ROUNDCUBE_VERSION}-complete.tar.gz",
            archive,
        )
        run(["rm", "-rf", str(dest)])
        run(["tar", "-xzf", str(archive), "-C", "/var/www"])
        extracted = Path(f"/var/www/roundcubemail-{ROUNDCUBE_VERSION}")
        if extracted.exists():
            extracted.rename(dest)

    sql_file = dest / "SQL/mysql.initial.sql"
    proc = run(["mysql", "-uroot", "roundcube", "-N", "-e", "SHOW TABLES LIKE 'users'"], check=False)
    if proc.returncode == 0 and "users" not in proc.stdout and sql_file.exists():
        run(["mysql", "-uroot", "roundcube"], input_text=sql_file.read_text(encoding="utf-8", errors="ignore"))

    config_dir = dest / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    write_file(
        config_dir / "config.inc.php",
        f"""<?php
$config['db_dsnw'] = 'mysql://roundcube:{sec['roundcube_db_password']}@localhost/roundcube';
$config['imap_host'] = 'ssl://127.0.0.1:993';
$config['smtp_host'] = 'tls://127.0.0.1:587';
$config['smtp_user'] = '%u';
$config['smtp_pass'] = '%p';
$config['imap_conn_options'] = array(
    'ssl' => array(
        'verify_peer' => false,
        'verify_peer_name' => false,
        'allow_self_signed' => true,
    ),
);
$config['smtp_conn_options'] = array(
    'ssl' => array(
        'verify_peer' => false,
        'verify_peer_name' => false,
        'allow_self_signed' => true,
    ),
);
$config['des_key'] = {php_quote(sec['roundcube_des_key'])};
?>
""",
    )
    run(["chown", "-R", "www-data:www-data", str(dest)])


def install_postfixadmin(settings: dict[str, str], sec: dict[str, str], admin_password: str | None) -> None:
    dest = Path("/var/www/postfixadmin")
    if not (dest / "public/index.php").exists():
        archive = Path(f"/tmp/postfixadmin-{POSTFIXADMIN_VERSION}.tar.gz")
        download(
            f"https://github.com/postfixadmin/postfixadmin/archive/refs/tags/postfixadmin-{POSTFIXADMIN_VERSION}.tar.gz",
            archive,
        )
        run(["rm", "-rf", str(dest)])
        run(["tar", "-xzf", str(archive), "-C", "/var/www"])
        extracted = Path(f"/var/www/postfixadmin-postfixadmin-{POSTFIXADMIN_VERSION}")
        if extracted.exists():
            extracted.rename(dest)

    setup_hash = "disabled"
    if admin_password:
        env = os.environ.copy()
        env["MAILSTACK_ADMIN_PASSWORD"] = admin_password
        proc = run(
            ["php", "-r", "echo password_hash(getenv('MAILSTACK_ADMIN_PASSWORD'), PASSWORD_DEFAULT);"],
            env=env,
        )
        setup_hash = proc.stdout.strip()

    write_file(
        dest / "config.local.php",
        f"""<?php
$CONF['configured'] = true;
$CONF['postfix_admin_url'] = {php_quote(postfixadmin_url(settings))};
$CONF['database_type'] = 'mysqli';
$CONF['database_host'] = 'localhost';
$CONF['database_user'] = 'postfixadmin';
$CONF['database_password'] = {php_quote(sec['postfixadmin_db_password'])};
$CONF['database_name'] = 'postfixadmin';
$CONF['setup_password'] = {php_quote(setup_hash)};
$CONF['encrypt'] = 'php_crypt:BLOWFISH:12:{{BLF-CRYPT}}';
$CONF['default_aliases'] = array();
?>
""",
    )
    (dest / "templates_c").mkdir(parents=True, exist_ok=True)
    run(["chown", "-R", "www-data:www-data", str(dest)])
    run(["chmod", "-R", "775", str(dest / "templates_c")])
    if (dest / "public/upgrade.php").exists():
        run(["php", str(dest / "public/upgrade.php")], check=False)
    reset_postfixadmin_password(settings["admin_email"], admin_password)
    run(["/usr/bin/python3", "/opt/mailstack/postfixadmin/patch_dns_page.py"])


def reset_postfixadmin_password(email: str, password: str | None) -> None:
    if not password:
        return
    cli = Path("/var/www/postfixadmin/scripts/postfixadmin-cli.php")
    common = [
        "--password",
        password,
        "--password2",
        password,
        "--superadmin",
        "1",
        "--active",
        "1",
    ]
    proc = run(
        ["php", str(cli), "admin", "update", email, *common],
        check=False,
        display=f"php {cli} admin update {email} --password [redacted] --password2 [redacted] --superadmin 1 --active 1",
    )
    if proc.returncode != 0:
        run(
            ["php", str(cli), "admin", "add", email, *common],
            display=f"php {cli} admin add {email} --password [redacted] --password2 [redacted] --superadmin 1 --active 1",
        )


def active_mail_domains() -> list[str]:
    proc = run(
        ["mysql", "-uroot", "postfixadmin", "-N", "-e", "SELECT domain FROM domain WHERE active = 1 ORDER BY domain"],
        check=False,
    )
    if proc.returncode != 0:
        return []
    domains = []
    for line in proc.stdout.splitlines():
        domain = line.strip()
        if domain and domain != "ALL":
            domains.append(domain)
    return domains


def sync_dkim_domains() -> None:
    for domain in active_mail_domains():
        run(["mailstack-dkim-domain", domain], check=False)


def configure_nginx(settings: dict[str, str], cert_file: Path, key_file: Path) -> None:
    mail_host = settings["mail_hostname"]
    roundcube_host = webmail_host(settings)
    pfa_host = admin_host(settings)
    roundcube_names = server_names(roundcube_host, mail_host, "_")
    pfa_url_at_root = pfa_host not in {roundcube_host, mail_host}
    acme_location = """
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/certbot;
        default_type text/plain;
    }
"""
    ssl_settings = f"""
    ssl_certificate {cert_file};
    ssl_certificate_key {key_file};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
"""
    roundcube_locations = f"""
    root /var/www/roundcube;
    index index.php index.html;
{acme_location}
    location / {{
        try_files $uri $uri/ /index.php;
    }}

    location ^~ /postfixadmin/ {{
        alias /var/www/postfixadmin/public/;
        index index.php;
        try_files $uri $uri/ /postfixadmin/index.php;
    }}

    location ~ ^/postfixadmin/(.+\\.php)$ {{
        alias /var/www/postfixadmin/public/$1;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $request_filename;
        fastcgi_pass unix:/run/php/php-fpm.sock;
    }}

    location ~ \\.php$ {{
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/php-fpm.sock;
    }}
"""
    postfixadmin_locations = f"""
    root /var/www/postfixadmin/public;
    index index.php index.html;
{acme_location}
    location / {{
        try_files $uri $uri/ /index.php;
    }}

    location ~ \\.php$ {{
        include snippets/fastcgi-php.conf;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        fastcgi_pass unix:/run/php/php-fpm.sock;
    }}
"""
    pfa_block = ""
    if pfa_url_at_root:
        pfa_block = f"""
server {{
    listen 80;
    server_name {server_names(pfa_host)};
{postfixadmin_locations}
}}

server {{
    listen 443 ssl;
    server_name {server_names(pfa_host)};
{ssl_settings}
{postfixadmin_locations}
}}
"""
    write_file(
        "/etc/nginx/sites-available/default",
        f"""server {{
    listen 80 default_server;
    server_name {roundcube_names};
{roundcube_locations}
}}

server {{
    listen 443 ssl default_server;
    server_name {roundcube_names};
{ssl_settings}
{roundcube_locations}
}}
{pfa_block}
""",
    )


def configure_web_tls(settings: dict[str, str]) -> None:
    ensure_self_signed_cert(settings)
    configure_nginx(settings, *active_cert_paths())
    reload_nginx()
    if try_letsencrypt(settings):
        configure_nginx(settings, *active_cert_paths())


def apply(settings: dict[str, str], password_only: bool) -> None:
    if password_only:
        if not settings.get("admin_email"):
            raise RuntimeError("Missing setting: admin_email")
        admin_password = read_admin_password() if ADMIN_PASSWORD_PATH.exists() else None
        sec = read_secrets()
        wait_mysql()
        ensure_database(settings, sec)
        install_postfixadmin(settings, sec, admin_password)
        print("PostfixAdmin password reset complete.")
        return

    for key in ["mail_hostname", "roundcube_domain", "postfixadmin_domain", "admin_email"]:
        if not settings.get(key):
            raise RuntimeError(f"Missing setting: {key}")
    admin_password = read_admin_password() if ADMIN_PASSWORD_PATH.exists() else None
    sec = read_secrets()
    wait_mysql()
    ensure_database(settings, sec)
    install_postfixadmin(settings, sec, admin_password)
    sync_dkim_domains()
    configure_postfix(settings, sec)
    configure_dovecot(sec)
    install_roundcube(sec)
    configure_web_tls(settings)
    run(["postfix", "reload"], check=False)
    run(["supervisorctl", "restart", "dovecot"], check=False)
    run(["supervisorctl", "restart", "nginx"], check=False)
    run(["supervisorctl", "restart", "php-fpm"], check=False)
    print("MailStack setup complete.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--password-only", action="store_true")
    args = parser.parse_args()
    apply(read_settings(), args.password_only)


if __name__ == "__main__":
    main()
