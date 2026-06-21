#!/usr/bin/env python3
from pathlib import Path

POSTFIXADMIN = Path("/var/www/postfixadmin")
PUBLIC = POSTFIXADMIN / "public"

DNS_PAGE = r'''<?php
require_once(dirname(__DIR__) . '/common.php');

if (function_exists('authentication_require_role')) {
    authentication_require_role('admin');
}

function mailstack_h($value) {
    return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8');
}

function mailstack_dkim_value($domain) {
    $path = "/etc/opendkim/keys/" . basename($domain) . "/default.txt";
    if (!is_readable($path)) {
        return "DKIM is generated automatically after this domain is active. Refresh shortly.";
    }
    $raw = file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    $joined = implode(' ', $raw);
    $joined = str_replace(array('"', '(', ')', "\t"), '', $joined);
    $joined = preg_replace('/\s+/', ' ', $joined);
    if (strpos($joined, 'v=DKIM1') !== false) {
        $parts = explode('TXT', $joined, 2);
        return trim(count($parts) === 2 ? $parts[1] : $joined);
    }
    return trim($joined);
}

function mailstack_setting($key, $default = '') {
    $db = '/data/state/mailstack.db';
    if (!is_readable($db)) {
        return $default;
    }
    try {
        $pdo = new PDO('sqlite:' . $db);
        $stmt = $pdo->prepare('SELECT value FROM settings WHERE key = ?');
        $stmt->execute(array($key));
        $value = $stmt->fetchColumn();
        return $value !== false ? $value : $default;
    } catch (Exception $e) {
        return $default;
    }
}

$domains = array();
try {
    $dbHost = isset($CONF['database_host']) ? $CONF['database_host'] : 'localhost';
    $pdo = new PDO(
        'mysql:host=' . $dbHost . ';dbname=' . $CONF['database_name'] . ';charset=utf8mb4',
        $CONF['database_user'],
        $CONF['database_password'],
        array(PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION)
    );
    $stmt = $pdo->query("SELECT domain FROM domain WHERE active = 1 ORDER BY domain");
    $domains = $stmt->fetchAll(PDO::FETCH_COLUMN);
} catch (Exception $e) {
    $domains = array();
}

$mailHost = mailstack_setting('mail_hostname', 'mail.example.com');
$roundcubeHost = mailstack_setting('roundcube_domain', $mailHost);
$postfixAdminHost = mailstack_setting('postfixadmin_domain', 'postfix.example.com');
$publicIp = mailstack_setting('public_ip', 'YOUR_SERVER_IP');
$serviceHosts = array_values(array_unique(array_filter(array($mailHost, $roundcubeHost, $postfixAdminHost))));

?><!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MailStack DNS Records</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #17202a; }
    h1 { margin-bottom: 6px; }
    p { color: #596273; }
    table { width: 100%; border-collapse: collapse; margin: 18px 0 34px; }
    th, td { text-align: left; border-bottom: 1px solid #d7dce3; padding: 10px; vertical-align: top; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; }
    .empty { padding: 14px; background: #fff7ed; border: 1px solid #fed7aa; border-radius: 6px; }
  </style>
</head>
<body>
  <h1>DNS Records</h1>
  <p>Copy these records to your DNS provider for each domain you add in PostfixAdmin.</p>
  <h2>Service hostnames</h2>
  <table>
    <thead><tr><th>Type</th><th>Name</th><th>Value</th></tr></thead>
    <tbody>
      <?php foreach ($serviceHosts as $host): ?>
        <tr><td><code>A</code></td><td><code><?php echo mailstack_h($host); ?></code></td><td><code><?php echo mailstack_h($publicIp); ?></code></td></tr>
      <?php endforeach; ?>
    </tbody>
  </table>
  <h2>Email domains</h2>
  <?php if (!$domains): ?>
    <div class="empty">No active domains found yet.</div>
  <?php endif; ?>
  <?php foreach ($domains as $domain): ?>
    <h2><?php echo mailstack_h($domain); ?></h2>
    <table>
      <thead><tr><th>Type</th><th>Name</th><th>Value</th></tr></thead>
      <tbody>
        <tr><td><code>A</code></td><td><code><?php echo mailstack_h($mailHost); ?></code></td><td><code><?php echo mailstack_h($publicIp); ?></code></td></tr>
        <tr><td><code>MX</code></td><td><code><?php echo mailstack_h($domain); ?></code></td><td><code>10 <?php echo mailstack_h($mailHost); ?></code></td></tr>
        <tr><td><code>TXT</code></td><td><code><?php echo mailstack_h($domain); ?></code></td><td><code>v=spf1 mx a ~all</code></td></tr>
        <tr><td><code>TXT</code></td><td><code>default._domainkey.<?php echo mailstack_h($domain); ?></code></td><td><code><?php echo mailstack_h(mailstack_dkim_value($domain)); ?></code></td></tr>
        <tr><td><code>TXT</code></td><td><code>_dmarc.<?php echo mailstack_h($domain); ?></code></td><td><code>v=DMARC1; p=quarantine; rua=mailto:postmaster@<?php echo mailstack_h($domain); ?></code></td></tr>
      </tbody>
    </table>
  <?php endforeach; ?>
</body>
</html>
'''

CONNECTION_PAGE = r'''<?php
require_once(dirname(__DIR__) . '/common.php');

if (function_exists('authentication_require_role')) {
    authentication_require_role('admin');
}

function mailstack_h($value) {
    return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8');
}

function mailstack_setting($key, $default = '') {
    $db = '/data/state/mailstack.db';
    if (!is_readable($db)) {
        return $default;
    }
    try {
        $pdo = new PDO('sqlite:' . $db);
        $stmt = $pdo->prepare('SELECT value FROM settings WHERE key = ?');
        $stmt->execute(array($key));
        $value = $stmt->fetchColumn();
        return $value !== false ? $value : $default;
    } catch (Exception $e) {
        return $default;
    }
}

$mailHost = mailstack_setting('mail_hostname', 'mail.example.com');
$roundcubeHost = mailstack_setting('roundcube_domain', $mailHost);
$postfixAdminHost = mailstack_setting('postfixadmin_domain', 'postfix.example.com');
$postfixAdminUrl = $postfixAdminHost === $roundcubeHost || $postfixAdminHost === $mailHost
    ? 'http://' . $postfixAdminHost . '/postfixadmin/'
    : 'http://' . $postfixAdminHost . '/';
$rows = array(
    array('SMTP submission', $mailHost, '587', 'STARTTLS. Use the full mailbox address as the username.'),
    array('SMTP over TLS', $mailHost, '465', 'Implicit TLS. Use the full mailbox address as the username.'),
    array('IMAP', $mailHost, '143', 'STARTTLS. Use the full mailbox address as the username.'),
    array('IMAPS', $mailHost, '993', 'Implicit TLS. Use the full mailbox address as the username.'),
    array('Webmail', 'http://' . $roundcubeHost . '/', '80', 'Roundcube mailbox login.'),
    array('PostfixAdmin', $postfixAdminUrl, '80', 'Domain and mailbox administration.')
);

?><!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MailStack Connection Details</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #17202a; }
    h1 { margin-bottom: 6px; }
    p { color: #596273; }
    table { width: 100%; border-collapse: collapse; margin: 18px 0 28px; }
    th, td { text-align: left; border-bottom: 1px solid #d7dce3; padding: 10px; vertical-align: top; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; }
    .note { padding: 14px; background: #f8fafc; border: 1px solid #d7dce3; border-radius: 6px; }
  </style>
</head>
<body>
  <h1>Connection Details</h1>
  <p>Use these values in email clients such as Apple Mail, Thunderbird, Outlook, or mobile mail apps.</p>
  <table>
    <thead><tr><th>Service</th><th>Server</th><th>Port</th><th>Security and login</th></tr></thead>
    <tbody>
      <?php foreach ($rows as $row): ?>
        <tr>
          <td><strong><?php echo mailstack_h($row[0]); ?></strong></td>
          <td><code><?php echo mailstack_h($row[1]); ?></code></td>
          <td><code><?php echo mailstack_h($row[2]); ?></code></td>
          <td><?php echo mailstack_h($row[3]); ?></td>
        </tr>
      <?php endforeach; ?>
    </tbody>
  </table>
  <div class="note">
    <p><strong>Mailbox username format:</strong> <code>user@yourdomain.com</code></p>
    <p><strong>Password:</strong> the mailbox password created in PostfixAdmin.</p>
  </div>
</body>
</html>
'''


def write_pages() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "mailstack_dns.php").write_text(DNS_PAGE, encoding="utf-8")
    (PUBLIC / "mailstack_connection.php").write_text(CONNECTION_PAGE, encoding="utf-8")


def patch_templates() -> None:
    links = [
        '<li><a href="mailstack_dns.php">DNS Records</a></li>',
        '<li><a href="mailstack_connection.php">Connection Details</a></li>',
    ]
    candidates = [
        POSTFIXADMIN / "templates/menu.tpl",
        POSTFIXADMIN / "templates/header.tpl",
        POSTFIXADMIN / "templates/main.tpl",
    ]
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        missing = [link for link in links if link not in text]
        if not missing:
            return
        if "</ul>" in text:
            path.write_text(text.replace("</ul>", "\n".join(missing) + "\n</ul>", 1), encoding="utf-8")
            return


if __name__ == "__main__":
    write_pages()
    patch_templates()
