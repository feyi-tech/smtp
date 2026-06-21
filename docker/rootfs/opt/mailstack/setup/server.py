#!/usr/bin/env python3
import html
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

STATE_DIR = Path("/data/state")
DB_PATH = STATE_DIR / "mailstack.db"
TOKEN_PATH = STATE_DIR / "setup.token"
APPLY = Path("/opt/mailstack/setup/apply_config.py")
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])$"
)


def token() -> str:
    return TOKEN_PATH.read_text(encoding="utf-8").strip()


def db() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            level TEXT NOT NULL,
            message TEXT NOT NULL
        )
        """
    )
    conn.commit()
    STATE_DIR.chmod(0o711)
    DB_PATH.chmod(0o644)
    return conn


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_settings(values: dict[str, str]) -> None:
    conn = db()
    with conn:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


def add_event(level: str, message: str) -> None:
    conn = db()
    with conn:
        conn.execute("INSERT INTO events(level, message) VALUES(?, ?)", (level, message))


def latest_settings() -> dict[str, str]:
    conn = db()
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}


def clean_hostname(value: str) -> str:
    return value.strip().lower().rstrip(".")


def valid_hostname(value: str) -> bool:
    return bool(HOSTNAME_RE.match(value))


def request_host(headers) -> str:
    raw = headers.get("X-Forwarded-Host") or headers.get("Host") or ""
    host = raw.split(",", 1)[0].strip()
    if host.startswith("[") and "]" in host:
        return host[1:].split("]", 1)[0]
    if host.count(":") == 1:
        return host.rsplit(":", 1)[0]
    return host


def public_ipv4(value: str) -> str:
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return ""
    if ip.version != 4 or ip.is_loopback or ip.is_private or ip.is_link_local:
        return ""
    return str(ip)


def remember_public_ip(value: str) -> None:
    ip = public_ipv4(value)
    if not ip:
        return
    conn = db()
    if get_setting(conn, "public_ip"):
        conn.close()
        return
    conn.close()
    set_settings({"public_ip": ip})


def webmail_host(settings: dict[str, str]) -> str:
    return settings.get("roundcube_domain") or settings.get("mail_hostname") or "mail.example.com"


def admin_host(settings: dict[str, str]) -> str:
    return settings.get("postfixadmin_domain") or settings.get("mail_hostname") or "postfix.example.com"


def postfixadmin_url(settings: dict[str, str]) -> str:
    host = admin_host(settings)
    if host in {webmail_host(settings), settings.get("mail_hostname", "")}:
        return f"https://{host}/postfixadmin/"
    return f"https://{host}/"


def run_apply(extra_args: list[str] | None = None) -> tuple[int, str]:
    args = ["/usr/bin/python3", str(APPLY)]
    if extra_args:
        args.extend(extra_args)
    proc = subprocess.run(args, text=True, capture_output=True, timeout=300)
    output = (proc.stdout or "") + (proc.stderr or "")
    add_event("info" if proc.returncode == 0 else "error", output[-4000:])
    return proc.returncode, output


def page(title: str, body: str, status: HTTPStatus = HTTPStatus.OK) -> tuple[int, bytes]:
    doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --ink: #17202a;
      --muted: #596273;
      --line: #d7dce3;
      --panel: #ffffff;
      --accent: #176b87;
      --bad: #b42318;
      --good: #087443;
    }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    main {{
      max-width: 1040px;
      margin: 0 auto;
      padding: 28px 18px 52px;
    }}
    h1 {{ font-size: 26px; margin: 0 0 8px; }}
    h2 {{ font-size: 18px; margin: 30px 0 12px; }}
    p {{ color: var(--muted); line-height: 1.5; }}
    form, section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      margin-top: 16px;
    }}
    label {{ display: block; font-weight: 650; margin: 14px 0 6px; }}
    input {{
      width: min(100%, 520px);
      box-sizing: border-box;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      font: inherit;
    }}
    button, .button {{
      display: inline-block;
      margin-top: 18px;
      padding: 10px 14px;
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: #fff;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
    }}
    code, pre {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 13px;
    }}
    pre {{
      overflow: auto;
      background: #111827;
      color: #e5e7eb;
      padding: 12px;
      border-radius: 6px;
    }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); }}
    th, td {{ text-align: left; border-bottom: 1px solid var(--line); padding: 10px; vertical-align: top; }}
    .ok {{ color: var(--good); font-weight: 700; }}
    .bad {{ color: var(--bad); font-weight: 700; }}
    .nav {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 18px 0; }}
  </style>
</head>
<body><main>{body}</main></body></html>"""
    return int(status), doc.encode("utf-8")


def nav(t: str) -> str:
    return f"""<div class="nav">
      <a class="button" href="/setup/{t}">Setup</a>
      <a class="button" href="/setup/{t}/dns">DNS Records</a>
      <a class="button" href="/setup/{t}/connection">Connection Details</a>
      <a class="button" href="/setup/{t}/password">Admin Password</a>
    </div>"""


def form_value(settings: dict[str, str], key: str) -> str:
    return html.escape(settings.get(key, ""))


def setup_form(t: str, message: str = "") -> tuple[int, bytes]:
    settings = latest_settings()
    webmail = webmail_host(settings)
    body = f"""
      <h1>MailStack Setup</h1>
      <p>This tokenized page configures Postfix, Dovecot, OpenDKIM, Roundcube, and PostfixAdmin inside the Docker mail container.</p>
      {nav(t)}
      {message}
      <form method="post" action="/setup/{t}">
        <label>Mail hostname</label>
        <input name="mail_hostname" required placeholder="mail.example.com" value="{form_value(settings, 'mail_hostname')}">

        <label>Roundcube website domain</label>
        <input name="roundcube_domain" required placeholder="mail.example.com" value="{form_value(settings, 'roundcube_domain')}">

        <label>PostfixAdmin website domain</label>
        <input name="postfixadmin_domain" required placeholder="postfix.example.com" value="{form_value(settings, 'postfixadmin_domain')}">

        <label>PostfixAdmin admin email</label>
        <input name="admin_email" required type="email" placeholder="admin@example.com" value="{form_value(settings, 'admin_email')}">

        <label>PostfixAdmin admin password</label>
        <input name="admin_password" required type="password" autocomplete="new-password">

        <button type="submit">Apply Mail Server Setup</button>
      </form>
      <section>
        <h2>Access after setup</h2>
        <p>SMTP/IMAP server: <code>{form_value(settings, 'mail_hostname') or 'mail.example.com'}</code></p>
        <p>Roundcube: <code>https://{html.escape(webmail)}/</code></p>
        <p>PostfixAdmin: <code>{html.escape(postfixadmin_url(settings))}</code></p>
      </section>
    """
    return page("MailStack Setup", body)


def dns_records_for(domain: str, mail_hostname: str, public_ip: str) -> list[tuple[str, str, str]]:
    records = [
        ("A", mail_hostname, public_ip or "YOUR_SERVER_IP"),
        ("MX", domain, f"10 {mail_hostname}"),
        ("TXT", domain, "v=spf1 mx a ~all"),
        ("TXT", f"_dmarc.{domain}", f"v=DMARC1; p=quarantine; rua=mailto:postmaster@{domain}"),
    ]
    txt_path = Path("/etc/opendkim/keys") / domain / "default.txt"
    if txt_path.exists():
        raw = txt_path.read_text(encoding="utf-8", errors="ignore")
        compact = " ".join(line.strip() for line in raw.splitlines() if "DKIM1" in line or "p=" in line)
        compact = compact.replace('"', "").replace("(", "").replace(")", "").replace("\t", " ")
        if "p=" in compact:
            value = compact.split("TXT", 1)[-1].strip() if "TXT" in compact else compact
            records.append(("TXT", f"default._domainkey.{domain}", value))
    else:
        records.append(("TXT", f"default._domainkey.{domain}", "DKIM key is generated after the domain exists in PostfixAdmin. Refresh this page after adding it."))
    return records


def read_domains() -> list[str]:
    domains = []
    try:
        proc = subprocess.run(
            ["mysql", "-uroot", "postfixadmin", "-N", "-e", "SELECT domain FROM domain WHERE active = 1"],
            text=True,
            capture_output=True,
            timeout=10,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                domain = line.strip()
                if domain and domain != "ALL" and domain not in domains:
                    domains.append(domain)
    except Exception:
        pass
    return domains


def service_dns_records(settings: dict[str, str], public_ip: str) -> list[tuple[str, str, str]]:
    value = public_ip or "YOUR_SERVER_IP"
    hosts = [
        settings.get("mail_hostname", "mail.example.com"),
        webmail_host(settings),
        admin_host(settings),
    ]
    records = []
    seen = set()
    for host in hosts:
        if host and host not in seen:
            records.append(("A", host, value))
            seen.add(host)
    return records


def dns_page(t: str) -> tuple[int, bytes]:
    settings = latest_settings()
    mail_hostname = settings.get("mail_hostname", "mail.example.com")
    public_ip = settings.get("public_ip", "YOUR_SERVER_IP")
    service_rows = [
        f"<tr><td><code>{html.escape(rtype)}</code></td><td><code>{html.escape(name)}</code></td>"
        f"<td><code>{html.escape(value)}</code></td></tr>"
        for rtype, name, value in service_dns_records(settings, public_ip)
    ]
    domain_rows = []
    for domain in read_domains():
        for rtype, name, value in dns_records_for(domain, mail_hostname, public_ip):
            domain_rows.append(
                f"<tr><td><code>{html.escape(rtype)}</code></td><td><code>{html.escape(name)}</code></td>"
                f"<td><code>{html.escape(value)}</code></td></tr>"
            )
    body = f"""
      <h1>DNS Records</h1>
      <p>Copy these records to the DNS provider for each domain you add in PostfixAdmin.</p>
      {nav(t)}
      <section>
        <h2>Service hostnames</h2>
        <table>
          <thead><tr><th>Type</th><th>Name</th><th>Value</th></tr></thead>
          <tbody>{''.join(service_rows)}</tbody>
        </table>
      </section>
      <section>
        <h2>Email domains</h2>
        <table>
          <thead><tr><th>Type</th><th>Name</th><th>Value</th></tr></thead>
          <tbody>{''.join(domain_rows) or '<tr><td colspan="3">No PostfixAdmin domains found yet.</td></tr>'}</tbody>
        </table>
      </section>
    """
    return page("MailStack DNS Records", body)


def connection_rows(settings: dict[str, str]) -> list[tuple[str, str, str, str]]:
    mail_hostname = settings.get("mail_hostname", "mail.example.com")
    return [
        ("SMTP submission", mail_hostname, "587", "STARTTLS, username is full mailbox address"),
        ("SMTP over TLS", mail_hostname, "465", "Implicit TLS, username is full mailbox address"),
        ("IMAP", mail_hostname, "143", "STARTTLS, username is full mailbox address"),
        ("IMAPS", mail_hostname, "993", "Implicit TLS, username is full mailbox address"),
        ("Webmail", f"https://{webmail_host(settings)}/", "443", "Roundcube mailbox login"),
        ("PostfixAdmin", postfixadmin_url(settings), "443", "Domain and mailbox administration"),
    ]


def connection_page(t: str) -> tuple[int, bytes]:
    settings = latest_settings()
    rows = [
        f"<tr><td><strong>{html.escape(service)}</strong></td><td><code>{html.escape(host)}</code></td>"
        f"<td><code>{html.escape(port)}</code></td><td>{html.escape(notes)}</td></tr>"
        for service, host, port, notes in connection_rows(settings)
    ]
    body = f"""
      <h1>Connection Details</h1>
      <p>Use these values in email clients such as Apple Mail, Thunderbird, Outlook, or mobile mail apps.</p>
      {nav(t)}
      <section>
        <table>
          <thead><tr><th>Service</th><th>Server</th><th>Port</th><th>Security and login</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </section>
      <section>
        <h2>Mailbox login format</h2>
        <p>Username: <code>user@yourdomain.com</code></p>
        <p>Password: the mailbox password created in PostfixAdmin.</p>
      </section>
    """
    return page("MailStack Connection Details", body)


def password_page(t: str, message: str = "") -> tuple[int, bytes]:
    settings = latest_settings()
    body = f"""
      <h1>PostfixAdmin Password</h1>
      <p>Reset the PostfixAdmin superadmin password without using SSH.</p>
      {nav(t)}
      {message}
      <form method="post" action="/setup/{t}/password">
        <label>Admin email</label>
        <input name="admin_email" required type="email" value="{form_value(settings, 'admin_email')}">
        <label>New password</label>
        <input name="admin_password" required type="password" autocomplete="new-password">
        <button type="submit">Reset Admin Password</button>
      </form>
    """
    return page("MailStack Admin Password", body)


class Handler(BaseHTTPRequestHandler):
    def send_page(self, response: tuple[int, bytes]) -> None:
        status, payload = response
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8")
        parsed = parse_qs(data)
        return {key: values[-1].strip() for key, values in parsed.items()}

    def valid_token(self, parts: list[str]) -> bool:
        return len(parts) >= 2 and parts[0] == "setup" and secrets.compare_digest(parts[1], token())

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            self.send_response(302)
            self.send_header("Location", f"/setup/{token()}")
            self.end_headers()
            return
        if not self.valid_token(parts):
            self.send_page(page("Not found", "<h1>Not found</h1>", HTTPStatus.NOT_FOUND))
            return
        remember_public_ip(request_host(self.headers))
        if len(parts) == 2:
            self.send_page(setup_form(parts[1]))
        elif len(parts) == 3 and parts[2] == "dns":
            self.send_page(dns_page(parts[1]))
        elif len(parts) == 3 and parts[2] == "connection":
            self.send_page(connection_page(parts[1]))
        elif len(parts) == 3 and parts[2] == "password":
            self.send_page(password_page(parts[1]))
        else:
            self.send_page(page("Not found", "<h1>Not found</h1>", HTTPStatus.NOT_FOUND))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if not self.valid_token(parts):
            self.send_page(page("Not found", "<h1>Not found</h1>", HTTPStatus.NOT_FOUND))
            return
        form = self.read_form()
        if len(parts) == 2:
            required = ["mail_hostname", "roundcube_domain", "postfixadmin_domain", "admin_email", "admin_password"]
            missing = [key for key in required if not form.get(key)]
            if missing:
                self.send_page(setup_form(parts[1], f"<p class='bad'>Missing: {', '.join(missing)}</p>"))
                return
            host_values = {
                "mail hostname": clean_hostname(form["mail_hostname"]),
                "Roundcube website domain": clean_hostname(form["roundcube_domain"]),
                "PostfixAdmin website domain": clean_hostname(form["postfixadmin_domain"]),
            }
            invalid_hosts = [label for label, value in host_values.items() if not valid_hostname(value)]
            if invalid_hosts:
                self.send_page(setup_form(parts[1], f"<p class='bad'>Invalid hostname: {', '.join(invalid_hosts)}</p>"))
                return
            if "@" not in form["admin_email"]:
                self.send_page(setup_form(parts[1], "<p class='bad'>Invalid admin email.</p>"))
                return
            remember_public_ip(request_host(self.headers))
            set_settings(
                {
                    "mail_hostname": host_values["mail hostname"],
                    "roundcube_domain": host_values["Roundcube website domain"],
                    "postfixadmin_domain": host_values["PostfixAdmin website domain"],
                    "admin_email": form["admin_email"].lower(),
                }
            )
            secret_payload = STATE_DIR / "admin-password.json"
            secret_payload.write_text(json.dumps({"admin_password": form["admin_password"]}), encoding="utf-8")
            secret_payload.chmod(0o600)
            code, output = run_apply()
            message = "<p class='ok'>Mail server setup was applied.</p>" if code == 0 else "<p class='bad'>Setup failed. Check logs below.</p>"
            message += f"<pre>{html.escape(output[-5000:])}</pre>"
            self.send_page(setup_form(parts[1], message))
        elif len(parts) == 3 and parts[2] == "password":
            if not form.get("admin_email") or not form.get("admin_password"):
                self.send_page(password_page(parts[1], "<p class='bad'>Email and password are required.</p>"))
                return
            set_settings({"admin_email": form["admin_email"].lower()})
            secret_payload = STATE_DIR / "admin-password.json"
            secret_payload.write_text(json.dumps({"admin_password": form["admin_password"]}), encoding="utf-8")
            secret_payload.chmod(0o600)
            code, output = run_apply(["--password-only"])
            message = "<p class='ok'>Password was reset.</p>" if code == 0 else "<p class='bad'>Password reset failed.</p>"
            message += f"<pre>{html.escape(output[-5000:])}</pre>"
            self.send_page(password_page(parts[1], message))
        else:
            self.send_page(page("Not found", "<h1>Not found</h1>", HTTPStatus.NOT_FOUND))

    def log_message(self, fmt: str, *args: object) -> None:
        print("setupd:", fmt % args, flush=True)


if __name__ == "__main__":
    db().close()
    remember_public_ip(os.environ.get("MAILSTACK_PUBLIC_HOST", ""))
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    print("setupd: listening on :8080", flush=True)
    server.serve_forever()
