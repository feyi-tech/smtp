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

function mailstack_preview($value, $limit = 96) {
    $text = (string)$value;
    if (strlen($text) <= $limit) {
        return $text;
    }
    return substr($text, 0, max(0, $limit - 3)) . '...';
}

function mailstack_dkim_value($domain) {
    $safe = basename($domain);
    $paths = array(
        "/data/state/dkim/" . $safe . ".default.txt",
        "/etc/opendkim/keys/" . $safe . "/default.txt"
    );
    $path = '';
    foreach ($paths as $candidate) {
        if (is_readable($candidate)) {
            $path = $candidate;
            break;
        }
    }
    if ($path === '') {
        return '';
    }
    $text = file_get_contents($path);
    $chunks = array();
    if (preg_match_all('/"([^"]*)"/', $text, $matches)) {
        $joined = implode('', $matches[1]);
    } else {
        $raw = file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
        $joined = implode(' ', $raw);
    }
    $joined = str_replace(array('(', ')', "\t"), '', $joined);
    $joined = preg_replace('/\s+/', ' ', $joined);
    if (strpos($joined, 'v=DKIM1') !== false) {
        $parts = explode('TXT', $joined, 2);
        return trim(count($parts) === 2 ? $parts[1] : $joined);
    }
    return trim($joined);
}

function mailstack_copy_field($value, $label = '') {
    $safe = mailstack_h($value);
    $preview = mailstack_h(mailstack_preview($value));
    $copy = mailstack_h($label !== '' ? $label : $value);
    $icon = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="10" height="10" rx="2"></rect><path d="M5 15V7a2 2 0 0 1 2-2h8"></path></svg>';
    return '<button class="copy-field" type="button" data-copy="' . $safe . '" title="' . $safe . '" aria-label="Copy ' . $copy . '"><code class="copy-preview">' . $preview . '</code><span class="copy-icon">' . $icon . '</span><span class="copy-status">Copied</span></button>';
}

function mailstack_missing_field($value) {
    return '<span class="missing-field">' . mailstack_h($value) . '</span>';
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
    :root { --ink: #17202a; --muted: #596273; --line: #d7dce3; --panel: #ffffff; --soft: #f8fafc; --accent: #176b87; --good: #087443; --warn: #9a5b00; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: var(--ink); background: #f6f7f9; }
    h1 { margin-bottom: 6px; }
    h2 { margin-top: 0; }
    p { color: var(--muted); }
    .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin: 18px 0 28px; }
    .panel-head { display: flex; align-items: end; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 12px; }
    label { display: block; font-weight: 700; margin-bottom: 6px; }
    select { min-width: min(100%, 360px); padding: 10px 12px; border: 1px solid var(--line); border-radius: 6px; font: inherit; background: #fff; }
    table { width: 100%; border-collapse: collapse; }
    .dns-table { table-layout: fixed; }
    .dns-table th:first-child, .dns-table td:first-child { width: 74px; }
    .dns-table th:nth-child(2), .dns-table td:nth-child(2) { width: 34%; }
    th, td { text-align: left; border-bottom: 1px solid var(--line); padding: 12px 10px; vertical-align: top; }
    th { color: #1f2937; font-size: 13px; text-transform: uppercase; letter-spacing: .04em; }
    .type-pill { display: inline-flex; min-width: 42px; justify-content: center; border: 1px solid var(--line); border-radius: 999px; padding: 3px 8px; font-weight: 800; font-size: 12px; background: var(--soft); }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; white-space: normal; overflow-wrap: anywhere; }
    .copy-field { width: 100%; max-width: 100%; min-width: 0; box-sizing: border-box; display: inline-flex; align-items: center; justify-content: space-between; gap: 10px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--ink); padding: 8px; text-align: left; cursor: pointer; }
    .copy-field:hover, .copy-field:focus { border-color: var(--accent); background: #eef7fa; outline: none; }
    .copy-field code { display: block; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .copy-icon { width: 18px; height: 18px; flex: 0 0 auto; color: var(--accent); }
    .copy-icon svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    .copy-status { display: none; flex: 0 0 auto; color: var(--good); font-weight: 800; font-size: 12px; }
    .copy-field.is-copied .copy-status { display: inline; }
    .copy-field.is-copied .copy-icon { display: none; }
    .missing-field { display: block; color: var(--warn); background: #fff8e6; border: 1px solid #f3d08a; border-radius: 6px; padding: 10px; overflow-wrap: anywhere; }
    .empty { padding: 14px; background: #fff7ed; border: 1px solid #fed7aa; border-radius: 6px; }
  </style>
</head>
<body>
  <h1>DNS Records</h1>
  <p>Copy these records to your DNS provider for each domain you add in PostfixAdmin.</p>

  <section class="panel">
    <h2>Service hostnames</h2>
    <table class="dns-table">
      <thead><tr><th>Type</th><th>Name</th><th>Value</th></tr></thead>
      <tbody>
        <?php foreach ($serviceHosts as $host): ?>
          <tr>
            <td><span class="type-pill">A</span></td>
            <td><?php echo mailstack_copy_field($host, 'record name'); ?></td>
            <td><?php echo mailstack_copy_field($publicIp, 'record value'); ?></td>
          </tr>
        <?php endforeach; ?>
      </tbody>
    </table>
  </section>

  <section class="panel">
    <div class="panel-head">
      <div>
        <h2>Email domains</h2>
        <p>Choose a domain to view only the DNS records for that domain.</p>
      </div>
      <?php if ($domains): ?>
        <div>
          <label for="domain-select">Domain</label>
          <select id="domain-select">
            <?php foreach ($domains as $index => $domain): ?>
              <option value="domain-panel-<?php echo (int)$index; ?>"><?php echo mailstack_h($domain); ?></option>
            <?php endforeach; ?>
          </select>
        </div>
      <?php endif; ?>
    </div>

    <?php if (!$domains): ?>
      <div class="empty">No active domains found yet.</div>
    <?php endif; ?>

    <?php foreach ($domains as $index => $domain): ?>
      <?php
        $dkimValue = mailstack_dkim_value($domain);
        $dkimMissing = $dkimValue === '';
        if ($dkimMissing) {
            $dkimValue = 'DKIM key is being generated automatically. Wait up to 60 seconds, then refresh this page.';
        }
      ?>
      <div class="domain-panel" id="domain-panel-<?php echo (int)$index; ?>" <?php echo $index === 0 ? '' : 'hidden'; ?>>
        <table class="dns-table">
          <thead><tr><th>Type</th><th>Name</th><th>Value</th></tr></thead>
          <tbody>
            <tr>
              <td><span class="type-pill">A</span></td>
              <td><?php echo mailstack_copy_field($mailHost, 'record name'); ?></td>
              <td><?php echo mailstack_copy_field($publicIp, 'record value'); ?></td>
            </tr>
            <tr>
              <td><span class="type-pill">MX</span></td>
              <td><?php echo mailstack_copy_field($domain, 'record name'); ?></td>
              <td><?php echo mailstack_copy_field('10 ' . $mailHost, 'record value'); ?></td>
            </tr>
            <tr>
              <td><span class="type-pill">TXT</span></td>
              <td><?php echo mailstack_copy_field($domain, 'record name'); ?></td>
              <td><?php echo mailstack_copy_field('v=spf1 ip4:' . $publicIp . ' mx ~all', 'record value'); ?></td>
            </tr>
            <tr>
              <td><span class="type-pill">TXT</span></td>
              <td><?php echo mailstack_copy_field('default._domainkey.' . $domain, 'record name'); ?></td>
              <td><?php echo $dkimMissing ? mailstack_missing_field($dkimValue) : mailstack_copy_field($dkimValue, 'record value'); ?></td>
            </tr>
            <tr>
              <td><span class="type-pill">TXT</span></td>
              <td><?php echo mailstack_copy_field('_dmarc.' . $domain, 'record name'); ?></td>
              <td><?php echo mailstack_copy_field('v=DMARC1; p=quarantine; rua=mailto:postmaster@' . $domain, 'record value'); ?></td>
            </tr>
          </tbody>
        </table>
      </div>
    <?php endforeach; ?>
  </section>

  <script>
    (function () {
      var select = document.getElementById('domain-select');
      if (select) {
        select.addEventListener('change', function () {
          document.querySelectorAll('.domain-panel').forEach(function (panel) {
            panel.hidden = panel.id !== select.value;
          });
        });
      }

      function fallbackCopy(text) {
        var area = document.createElement('textarea');
        area.value = text;
        area.setAttribute('readonly', 'readonly');
        area.style.position = 'fixed';
        area.style.left = '-9999px';
        document.body.appendChild(area);
        area.select();
        try { document.execCommand('copy'); } finally { document.body.removeChild(area); }
      }

      document.addEventListener('click', function (event) {
        var field = event.target.closest('[data-copy]');
        if (!field) return;
        var text = field.getAttribute('data-copy');
        var done = function () {
          document.querySelectorAll('.copy-field.is-copied').forEach(function (item) {
            item.classList.remove('is-copied');
          });
          field.classList.add('is-copied');
          window.setTimeout(function () { field.classList.remove('is-copied'); }, 1400);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done).catch(function () { fallbackCopy(text); done(); });
        } else {
          fallbackCopy(text);
          done();
        }
      });
    })();
  </script>
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
    ? 'https://' . $postfixAdminHost . '/postfixadmin/'
    : 'https://' . $postfixAdminHost . '/';
$rows = array(
    array('SMTP submission', $mailHost, '587', 'STARTTLS. Use the full mailbox address as the username.'),
    array('SMTP over TLS', $mailHost, '465', 'Implicit TLS. Use the full mailbox address as the username.'),
    array('IMAP', $mailHost, '143', 'STARTTLS. Use the full mailbox address as the username.'),
    array('IMAPS', $mailHost, '993', 'Implicit TLS. Use the full mailbox address as the username.'),
    array('Webmail', 'https://' . $roundcubeHost . '/', '443', 'Roundcube mailbox login.'),
    array('PostfixAdmin', $postfixAdminUrl, '443', 'Domain and mailbox administration.')
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
