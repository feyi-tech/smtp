#!/bin/bash
set -e

CONFIG="mail.conf"

log() {
  echo ""
  echo "========== $1 =========="
}

# ---------------- SAFE SERVICE STOP ----------------
stop_service() {
  S=$1
  echo "Stopping $S..."
  timeout 10 systemctl stop $S 2>/dev/null || true
  sleep 2
  pkill -9 -f $S 2>/dev/null || true
  systemctl reset-failed $S 2>/dev/null || true
}

# ---------------- ENSURE NGINX HEALTH ----------------
ensure_nginx() {
  log "Ensuring nginx is healthy"

  if [ ! -f "/etc/nginx/nginx.conf" ]; then
    echo "nginx config missing → reinstalling nginx"

    DEBIAN_FRONTEND=noninteractive apt purge -y nginx nginx-common nginx-core || true
    DEBIAN_FRONTEND=noninteractive apt install -y nginx
  fi

  if [ ! -f "/etc/nginx/nginx.conf" ]; then
    echo "Creating fallback nginx config"
    cat > /etc/nginx/nginx.conf <<EOF
events {}
http {
    server {
        listen 80 default_server;
        location / {
            return 200 "nginx healthy";
        }
    }
}
EOF
  fi
}

# ---------------- ESCAPE SQL PASSWORD ----------------
escape_sql() {
  echo "$1" | sed "s/'/''/g"
}

# ---------------- INIT ----------------
if [ "$1" == "--init" ]; then
cat > $CONFIG <<EOF
DOMAIN=example.com
MAIL_HOSTNAME=mail.example.com
MYSQL_ROOT_PASSWORD=rootpassword
ROUNDCUBE_DB_PASSWORD=roundcubepass
POSTFIXADMIN_DB_PASSWORD=postfixadminpass
ADMIN_EMAIL=you@gmail.com
ADMIN_PASS=superStrongPassword123!
TIMEZONE=Africa/Lagos
CF_TOKEN=
CF_ZONE_ID=
EOF
echo "Created mail.conf"
exit 0
fi

# ---------------- RESET ----------------
if [ "$1" == "--reset" ]; then
log "Resetting system (SAFE)"

pkill -f dkim-watcher.sh 2>/dev/null || true
if command -v mysql_config_editor >/dev/null 2>&1; then
  mysql_config_editor remove --login-path=local 2>/dev/null || true
fi

stop_service mysql
stop_service nginx
stop_service postfix
stop_service dovecot

echo "Purging packages..."

DEBIAN_FRONTEND=noninteractive apt purge -y \
expect postfix dovecot-* mysql-server nginx nginx-common nginx-core \
php* certbot opendkim || true

echo "Removing unused dependencies..."
DEBIAN_FRONTEND=noninteractive apt autoremove -y

echo "Cleaning application data..."

rm -rf /etc/postfix /etc/dovecot /etc/opendkim \
/var/www/* /var/lib/mysql /etc/letsencrypt /var/log/letsencrypt \
/usr/local/bin/add-domain-dkim.sh /usr/local/bin/dkim-watcher.sh /usr/local/bin/mail-dns || true

echo "Reset complete"
exit 0
fi

# ---------------- LOAD ----------------
[ -f $CONFIG ] || { echo "Run --init first"; exit 1; }
source $CONFIG

log "Updating system"
apt update -y

hostnamectl set-hostname $MAIL_HOSTNAME

# ---------------- INSTALL ----------------
log "Installing packages"

DEBIAN_FRONTEND=noninteractive apt install -y \
expect postfix dovecot-core dovecot-imapd dovecot-mysql \
mysql-server nginx php-fpm php-imap php-mysql php-cli php-curl php-xml php-mbstring php-mysqlnd php-opcache php-bz2 php-gmp \
php-intl php-zip php-gd php-imagick unzip certbot python3-certbot-nginx \
opendkim opendkim-tools

PHP_VERSION=$(php -r "echo PHP_MAJOR_VERSION.'.'.PHP_MINOR_VERSION;")

# ---------------- ENSURE NGINX BASE ----------------
ensure_nginx

# ---------------- MYSQL ----------------
log "Configuring MySQL"

log "Configuring MySQL secure login"

systemctl start mysql || true

if ! mysql_config_editor print --login-path=local >/dev/null 2>&1; then
  echo "Setting MySQL login-path..."

  expect <<EOF
spawn mysql_config_editor set --login-path=local --user=root --password
expect "Enter password:"
send "$MYSQL_ROOT_PASSWORD\r"
expect eof
EOF

else
  echo "MySQL login-path already exists → skipping"
fi

ESCAPED_MYSQL_ROOT_PASS=$(escape_sql "$MYSQL_ROOT_PASSWORD")
ESCAPED_ROUNDCUBE_PASS=$(escape_sql "$ROUNDCUBE_DB_PASSWORD")
ESCAPED_POSTFIXADMIN_PASS=$(escape_sql "$POSTFIXADMIN_DB_PASSWORD")

if mysql -u root -e "SELECT 1" >/dev/null 2>&1; then
  echo "Setting root password"
  mysql -u root <<EOF
ALTER USER 'root'@'localhost' IDENTIFIED BY '$ESCAPED_MYSQL_ROOT_PASS';
FLUSH PRIVILEGES;
EOF
fi

MYSQL="mysql --login-path=local"

$MYSQL -e "CREATE DATABASE IF NOT EXISTS roundcube;"
$MYSQL -e "CREATE DATABASE IF NOT EXISTS postfixadmin;"

$MYSQL -e "CREATE USER IF NOT EXISTS 'roundcube'@'localhost' IDENTIFIED BY '$ESCAPED_ROUNDCUBE_PASS';"
$MYSQL -e "CREATE USER IF NOT EXISTS 'postfixadmin'@'localhost' IDENTIFIED BY '$ESCAPED_POSTFIXADMIN_PASS';"

$MYSQL -e "GRANT ALL ON roundcube.* TO 'roundcube'@'localhost';"
$MYSQL -e "GRANT ALL ON postfixadmin.* TO 'postfixadmin'@'localhost';"
$MYSQL -e "FLUSH PRIVILEGES;"

# ---------------- POSTFIX (VIRTUAL DOMAINS FIX) ----------------
log "Configuring Postfix for Virtual Mailboxes"

# 1. Basic Identity
postconf -e "myhostname = $MAIL_HOSTNAME"
postconf -e "mydomain = $DOMAIN"
postconf -e "myorigin = \$mydomain"
postconf -e "inet_interfaces = all"
postconf -e "inet_protocols = ipv4"

# 2. THE FIX: Tell Postfix NOT to treat the domain as a local system domain
# Only localhost should be in mydestination
postconf -e "mydestination = \$myhostname, localhost.\$mydomain, localhost"

# 3. Virtual Mailbox Settings (Database Lookups)
postconf -e "virtual_mailbox_domains = proxy:mysql:/etc/postfix/sql-domains.cf"
postconf -e "virtual_mailbox_maps = proxy:mysql:/etc/postfix/sql-accounts.cf"
postconf -e "virtual_alias_maps = proxy:mysql:/etc/postfix/sql-aliases.cf"

# 4. Hand off mail to Dovecot for storage
postconf -e "virtual_transport = lmtp:unix:private/dovecot-lmtp"

# 5. SASL Auth (Let users log in to send mail)
postconf -e "smtpd_sasl_type = dovecot"
postconf -e "smtpd_sasl_path = private/auth"
postconf -e "smtpd_sasl_auth_enable = yes"
postconf -e "smtpd_recipient_restrictions = permit_sasl_authenticated,permit_mynetworks,reject_unauth_destination"

# 6. ENABLE SUBMISSION (Port 587) - Fixes Roundcube SMTP Error
# This appends the submission service to master.cf
if ! grep -q "submission inet" /etc/postfix/master.cf; then
  cat >> /etc/postfix/master.cf <<EOF

submission inet n       -       y       -       -       smtpd
  -o syslog_name=postfix/submission
  -o smtpd_tls_security_level=may
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_sasl_type=dovecot
  -o smtpd_sasl_path=private/auth
  -o smtpd_reject_unlisted_recipient=no
  -o smtpd_recipient_restrictions=permit_sasl_authenticated,reject
  -o milter_macro_daemon_name=ORIGINATING
EOF
fi

systemctl restart postfix

# ---------------- DOVECOT (VIRTUAL MAILBOX SETUP) ----------------
log "Configuring Dovecot for PostfixAdmin Virtual Users"

# 1. Create a dedicated system user for virtual mail storage
if ! getent group vmail > /dev/null; then groupadd -g 5000 vmail; fi
if ! getent passwd vmail > /dev/null; then 
    useradd -u 5000 -g vmail -s /usr/sbin/nologin -d /var/vmail vmail
fi
mkdir -p /var/vmail
chown -R vmail:vmail /var/vmail

# 2. Basic Configuration (10-auth.conf)
# Disable system auth, enable SQL auth, and set mechanisms
sed -i 's/^#disable_plaintext_auth.*/disable_plaintext_auth = yes/' /etc/dovecot/conf.d/10-auth.conf
sed -i 's/^auth_mechanisms.*/auth_mechanisms = plain login/' /etc/dovecot/conf.d/10-auth.conf
sed -i 's/!include auth-system.conf.ext/#!include auth-system.conf.ext/' /etc/dovecot/conf.d/10-auth.conf
sed -i 's/#!include auth-sql.conf.ext/!include auth-sql.conf.ext/' /etc/dovecot/conf.d/10-auth.conf

# 3. Mail Location (10-mail.conf)
# Point to /var/vmail/domain/user and set UID/GID boundaries
sed -i 's|^mail_location.*|mail_location = maildir:/var/vmail/%d/%n/Maildir|' /etc/dovecot/conf.d/10-mail.conf
echo "first_valid_uid = 5000" >> /etc/dovecot/conf.d/10-mail.conf
echo "last_valid_uid = 5000" >> /etc/dovecot/conf.d/10-mail.conf

# 4. SQL Configuration (dovecot-sql.conf.ext)
# The user_query is CRITICAL. It maps the DB user to the filesystem.
cat > /etc/dovecot/dovecot-sql.conf.ext <<EOF
driver = mysql
connect = host=127.0.0.1 dbname=postfixadmin user=postfixadmin password=$POSTFIXADMIN_DB_PASSWORD
default_pass_scheme = BLF-CRYPT
password_query = SELECT username AS user, password FROM mailbox WHERE username='%u' AND active='1'
user_query = SELECT '/var/vmail/%d/%n' AS home, 5000 AS uid, 5000 AS gid FROM mailbox WHERE username='%u' AND active='1'
EOF

# 5. Master Configuration (10-master.conf)
# This allows Postfix to "talk" to Dovecot for authentication and delivery
cat > /etc/dovecot/conf.d/10-master.conf <<EOF
service imap-login {
  inet_listener imap {
    port = 143
  }
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

service stats {
  unix_listener stats-reader {
    user = www-data
    group = www-data
    mode = 0660
  }
  unix_listener stats-writer {
    user = www-data
    group = www-data
    mode = 0660
  }
}
EOF

# 6. Permissions & Permissions Fixes
usermod -aG dovecot www-data
chown -R vmail:vmail /var/vmail
chmod -R 770 /var/vmail

# Restart Dovecot
systemctl restart dovecot

# ---------------- OPENDKIM ----------------
log "Configuring DKIM"

mkdir -p /etc/opendkim/keys
touch /etc/opendkim/key.table
touch /etc/opendkim/signing.table
touch /etc/opendkim/trusted.hosts

grep -q "KeyTable" /etc/opendkim.conf || echo "KeyTable /etc/opendkim/key.table" >> /etc/opendkim.conf
grep -q "SigningTable" /etc/opendkim.conf || echo "SigningTable /etc/opendkim/signing.table" >> /etc/opendkim.conf
grep -q "InternalHosts" /etc/opendkim.conf || echo "InternalHosts /etc/opendkim/trusted.hosts" >> /etc/opendkim.conf

# DKIM auto script
if [ ! -f "/usr/local/bin/add-domain-dkim.sh" ]; then
log "Installing add-domain-dkim..."
# ---------------- DKIM, SPF & DMARC AUTO-DNS (CLOUDFLARE) ----------------
log "Installing updated DNS automation for Cloudflare"

cat > /usr/local/bin/add-domain-dkim.sh <<EOF
#!/bin/bash
DOMAIN=\$1
SELECTOR=default
DIR=/etc/opendkim/keys/\$DOMAIN
CF_TOKEN="$CF_TOKEN"
CF_ZONE_ID="$CF_ZONE_ID"

# Ensure domain is provided
if [ -z "\$DOMAIN" ]; then
    echo "Usage: add-domain-dkim.sh domain.com"
    exit 1
fi

mkdir -p \$DIR

# 1. Generate DKIM Keys if missing
if [ ! -f "\$DIR/\$SELECTOR.private" ]; then
    opendkim-genkey -D \$DIR -d \$DOMAIN -s \$SELECTOR
    chown -R opendkim:opendkim \$DIR
fi

# 2. Extract and Clean the DKIM Public Key for API
# We strip newlines, tabs, spaces, and parentheses
PUB_KEY=\$(cat "\$DIR/\$SELECTOR.txt" | grep -v "Record for" | tr -d '\n\t "()' | cut -d';' -f1,2)
DKIM_VALUE="v=DKIM1; k=rsa; p=\${PUB_KEY#*p=}"

# 3. Define SPF and DMARC Values
SPF_VALUE="v=spf1 a mx ~all"
DMARC_VALUE="v=DMARC1; p=quarantine; rua=mailto:postmaster@\$DOMAIN"

# 4. Function to Push to Cloudflare API
push_dns() {
    local TYPE=\$1
    local NAME=\$2
    local CONTENT=\$3
    
    # ONLY RUN IF CLOUDFLARE CREDENTIALS ARE SET
    if [[ -n "\$CF_TOKEN" && -n "\$CF_ZONE_ID" && "\$CF_TOKEN" != "your_cloudflare_api_token" ]]; then
        echo "Pushing \$TYPE record for \$NAME to Cloudflare..."
        RESULT=\$(curl -s -X POST "https://api.cloudflare.com/client/v4/zones/\$CF_ZONE_ID/dns_records" \
             -H "Authorization: Bearer \$CF_TOKEN" \
             -H "Content-Type: application/json" \
             --data "{
                \"type\":\"\$TYPE\",
                \"name\":\"\$NAME\",
                \"content\":\"\$CONTENT\",
                \"ttl\":3600,
                \"proxied\":false
             }")
             
        if echo "\$RESULT" | grep -q "\"success\":true"; then
            echo "✅ \$TYPE record for \$NAME updated"
        else
            echo "❌ Cloudflare API Error for \$NAME: \$(echo \$RESULT | grep -o '\"message\":\"[^\"]*\"')"
        fi
    else
        echo "⚠️ Cloudflare credentials not set in mail.conf. Skipping DNS push for \$DOMAIN."
        echo "Manual record needed for \$NAME: \$CONTENT"
    fi
}

# 5. Execute API Calls (Conditional)
push_dns "TXT" "\$SELECTOR._domainkey.\$DOMAIN" "\$DKIM_VALUE"
push_dns "TXT" "\$DOMAIN" "\$SPF_VALUE"
push_dns "TXT" "_dmarc.\$DOMAIN" "\$DMARC_VALUE"

# 6. Update local OpenDKIM tables (Always run locally)
grep -q "\$DOMAIN" /etc/opendkim/key.table || \
echo "\$SELECTOR._domainkey.\$DOMAIN \$DOMAIN:\$SELECTOR:\$DIR/\$SELECTOR.private" >> /etc/opendkim/key.table

grep -q "\$DOMAIN" /etc/opendkim/signing.table || \
echo "*@\$DOMAIN \$SELECTOR._domainkey.\$DOMAIN" >> /etc/opendkim/signing.table

grep -q "\$DOMAIN" /etc/opendkim/trusted.hosts || \
echo "\$DOMAIN" >> /etc/opendkim/trusted.hosts

systemctl restart opendkim
EOF

chmod +x /usr/local/bin/add-domain-dkim.sh
else
  echo "add-domain-dkim already exists → skipping"
fi

# ---------------- DKIM WATCHER (AUTO) ----------------
if [ ! -f "/usr/local/bin/dkim-watcher.sh" ]; then
log "Installing and Starting DKIM auto-watcher..."
cat > /usr/local/bin/dkim-watcher.sh <<'EOF'
#!/bin/bash

MYSQL_CMD="mysql --login-path=local postfixadmin -N -e"

while true; do
  DOMAINS=$($MYSQL_CMD "SELECT domain FROM domain;")

  for D in $DOMAINS; do
    if [ ! -f "/etc/opendkim/keys/$D/default.private" ]; then
      echo "[DKIM] Creating for $D"
      /usr/local/bin/add-domain-dkim.sh "$D"
    fi
  done

  sleep 60
done
EOF

chmod +x /usr/local/bin/dkim-watcher.sh
nohup /usr/local/bin/dkim-watcher.sh >/var/log/dkim-watcher.log 2>&1 &
else
  echo "DKIM watcher already running → skipping"
fi

# ---------------- DNS HELPER SCRIPT ----------------
if [ ! -f "/usr/local/bin/mail-dns" ]; then
  log "Installing DNS helper..."

  cat > /usr/local/bin/mail-dns <<'EOF'
#!/bin/bash

DOMAIN=$1
SELECTOR=default
DKIM_TXT="/etc/opendkim/keys/$DOMAIN/${SELECTOR}.txt"

if [ -z "$DOMAIN" ]; then
  echo "❌ Usage: mail-dns yourdomain.com"
  exit 1
fi

if [ ! -f "$DKIM_TXT" ]; then
  echo "❌ DKIM not generated for $DOMAIN"
  echo "👉 Run: add-domain-dkim.sh $DOMAIN"
  exit 1
fi

echo ""
echo "🌐 DNS RECORDS FOR $DOMAIN"
echo "----------------------------------"

echo ""
echo "📌 SPF RECORD:"
echo "Type: TXT"
echo "Name: @"
echo 'Value: v=spf1 mx a ~all'

echo ""
echo "📌 DKIM RECORD:"
echo "Type: TXT"
echo "Name: ${SELECTOR}._domainkey"
echo "Value:"
cat "$DKIM_TXT"

echo ""
echo "📌 DMARC (recommended):"
echo "Type: TXT"
echo "Name: _dmarc"
echo "Value: v=DMARC1; p=quarantine; rua=mailto:postmaster@$DOMAIN"

echo ""
EOF

  chmod +x /usr/local/bin/mail-dns
else
  echo "mail-dns already exists → skipping"
fi

# ---------------- ROUNDCUBE ----------------
log "Installing Roundcube"

cd /var/www

if [ ! -d "roundcube" ]; then
  wget -q https://github.com/roundcube/roundcubemail/releases/download/1.6.6/roundcubemail-1.6.6-complete.tar.gz
  tar -xzf roundcubemail-1.6.6-complete.tar.gz
  mv roundcubemail-1.6.6 roundcube
fi

# Import DB only if tables don't exist
if ! $MYSQL roundcube -e "SHOW TABLES LIKE 'users';" | grep -q users; then
  echo "Initializing Roundcube database..."
  $MYSQL roundcube < /var/www/roundcube/SQL/mysql.initial.sql
else
  echo "Roundcube DB already initialized → skipping"
fi

# ---------------- ROUNDCUBE CONFIGURATION (VIA SAMPLE) ----------------
log "Configuring Roundcube using sample file"

CONFIG_FILE="/var/www/roundcube/config/config.inc.php"

cd /var/www/roundcube/config

# 1. Copy the sample to the real config file
cp config.inc.php.sample config.inc.php

# URL Encode the password so special chars don't break the DSN string
ENCODED_PASS=$(php -r "echo rawurlencode('$ROUNDCUBE_DB_PASSWORD');")

# 2. Use 'sed' to replace the placeholder values with your real config
# We use | as a delimiter in sed because the DB string contains slashes /
sed -i "s|\$config\['db_dsnw'\] = .*|\$config['db_dsnw'] = 'mysql://roundcube:$ENCODED_PASS@localhost/roundcube';|" config.inc.php

# 3. Set the IMAP and SMTP hosts (Fixes the login and connection errors)
sed -i "s|\$config\['imap_host'\] = .*|\$config['imap_host'] = 'localhost:143';|" config.inc.php
sed -i "s|\$config\['smtp_host'\] = .*|\$config['smtp_host'] = 'localhost:587';|" config.inc.php

# 4. Set SMTP user/pass to use the logged-in user credentials
sed -i "s|\$config\['smtp_user'\] = .*|\$config['smtp_user'] = '%u';|" config.inc.php
sed -i "s|\$config\['smtp_pass'\] = .*|\$config['smtp_pass'] = '%p';|" config.inc.php

# 5. Generate a random DES key (Required for security)
DES_KEY=$(openssl rand -base64 24)
sed -i "s|\$config\['des_key'\] = .*|\$config['des_key'] = '$DES_KEY';|" config.inc.php

chown www-data:www-data config.inc.php

chown -R www-data:www-data /var/www/roundcube

# ---------------- POSTFIXADMIN ----------------
log "Installing PostfixAdmin"

cd /var/www

if [ ! -d "postfixadmin" ]; then
  wget -q https://github.com/postfixadmin/postfixadmin/archive/refs/tags/postfixadmin-3.3.13.tar.gz
  tar -xzf postfixadmin-3.3.13.tar.gz
  mv postfixadmin-postfixadmin-3.3.13 postfixadmin
fi

# -------- GENERATE SETUP PASSWORDS --------
# Setup hash (safe)
SETUP_HASH=$(ADMIN_PASS="$ADMIN_PASS" php -r 'echo password_hash(getenv("ADMIN_PASS"), PASSWORD_DEFAULT);')
SETUP_HASH=$(echo "$SETUP_HASH" | tr -d '\n')

# -------- CONFIG FILE --------
cat > /var/www/postfixadmin/config.local.php <<EOF
<?php
\$CONF['database_type'] = 'mysqli';
\$CONF['database_user'] = 'postfixadmin';
\$CONF['database_password'] = '$ESCAPED_POSTFIXADMIN_PASS';
\$CONF['database_name'] = 'postfixadmin';
\$CONF['setup_password'] = '$SETUP_HASH';
\$CONF['encrypt'] = 'dovecot:BLF-CRYPT';
\$CONF['dovecotpw'] = "/usr/bin/doveadm pw -r 12";
\$CONF['configured'] = true;
?>
EOF

chown -R www-data:www-data /var/www/postfixadmin
mkdir -p /var/www/postfixadmin/templates_c
chmod -R 777 /var/www/postfixadmin/templates_c

# -------- CREATE ADMIN USER (CLI METHOD) --------
log "Creating admin user via PostfixAdmin CLI"

cd /var/www/postfixadmin

php scripts/postfixadmin-cli.php admin add $ADMIN_EMAIL --superadmin 1 --active 1 --password "$ADMIN_PASS" --password2 "$ADMIN_PASS" || true

# Ensure DB is initialized
#php public/upgrade.php >/dev/null 2>&1 || true

# ---------------- NGINX CONFIG ----------------
log "Configuring nginx safely"

rm -f /etc/nginx/sites-enabled/default || true

if [ ! -f /etc/nginx/sites-available/mail ]; then
  cat > /etc/nginx/sites-available/mail <<EOF
server {
    listen 80;
    server_name $MAIL_HOSTNAME;

    root /var/www/roundcube;
    index index.php;

    location / {
        try_files \$uri \$uri/ /index.php;
    }

    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/php$PHP_VERSION-fpm.sock;
    }
}
EOF
fi

if [ ! -f /etc/nginx/sites-available/postfixadmin ]; then
cat > /etc/nginx/sites-available/postfixadmin <<EOF
server {
    listen 80;
    server_name postfix.$DOMAIN;

    root /var/www/postfixadmin/public;
    index index.php;

    location / {
        try_files \$uri \$uri/ /index.php;
    }

    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/php$PHP_VERSION-fpm.sock;
    }
}
EOF
fi

ln -sf /etc/nginx/sites-available/mail /etc/nginx/sites-enabled/mail
ln -sf /etc/nginx/sites-available/postfixadmin /etc/nginx/sites-enabled/postfixadmin

echo "Testing nginx config..."

if nginx -t; then
  systemctl restart nginx
else
  echo "❌ nginx config invalid → fallback mode"
  echo "events{} http{server{listen 80;}}" > /etc/nginx/nginx.conf
  systemctl restart nginx
fi

# ---------------- SSL ----------------
log "SSL Setup"

CERT_PATH="/etc/letsencrypt/live/$MAIL_HOSTNAME/fullchain.pem"

should_issue_cert() {
  # Cert does not exist
  if [ ! -f "$CERT_PATH" ]; then
    return 0
  fi

  # Check if cert expires in less than 30 days
  EXPIRY_DATE=$(openssl x509 -enddate -noout -in "$CERT_PATH" | cut -d= -f2)
  EXPIRY_SECONDS=$(date -d "$EXPIRY_DATE" +%s)
  NOW_SECONDS=$(date +%s)

  DAYS_LEFT=$(( (EXPIRY_SECONDS - NOW_SECONDS) / 86400 ))

  echo "SSL certificate expires in $DAYS_LEFT days"

  if [ "$DAYS_LEFT" -lt 30 ]; then
    return 0
  fi

  return 1
}

if ping -c 1 $MAIL_HOSTNAME &> /dev/null; then
  if should_issue_cert; then
    echo "Issuing/renewing SSL certificate..."
    certbot certonly --nginx -d $MAIL_HOSTNAME -d postfix.$DOMAIN \
      --non-interactive --agree-tos -m $ADMIN_EMAIL --redirect || true
  else
    echo "SSL certificate is still valid → skipping"
  fi
else
  echo "Skipping SSL (DNS not ready)"
fi

if [ -f "$CERT_PATH" ]; then
  # -------- Roundcube SSL --------
  if [ ! -f /etc/nginx/sites-available/mail-ssl ]; then
    log "Creating Roundcube SSL-only nginx config"
    cat > /etc/nginx/sites-available/mail-ssl <<EOF
server {
    listen 443 ssl;
    server_name $MAIL_HOSTNAME;

    ssl_certificate /etc/letsencrypt/live/$MAIL_HOSTNAME/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$MAIL_HOSTNAME/privkey.pem;

    root /var/www/roundcube;
    index index.php;

    location / {
        try_files \$uri \$uri/ /index.php;
    }

    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/php$PHP_VERSION-fpm.sock;
    }
}
EOF
  fi

  # -------- PostfixAdmin SSL --------
  if [ ! -f /etc/nginx/sites-available/postfixadmin-ssl ]; then
    log "Creating Postfixadmin SSL-only nginx config"
    cat > /etc/nginx/sites-available/postfixadmin-ssl <<EOF
server {
    listen 443 ssl;
    server_name postfix.$DOMAIN;

    ssl_certificate /etc/letsencrypt/live/$MAIL_HOSTNAME/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$MAIL_HOSTNAME/privkey.pem;

    root /var/www/postfixadmin/public;
    index index.php;

    location / {
        try_files \$uri \$uri/ /index.php;
    }

    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/php$PHP_VERSION-fpm.sock;
    }
}
EOF
  fi

  ln -sf /etc/nginx/sites-available/mail-ssl /etc/nginx/sites-enabled/mail-ssl
  ln -sf /etc/nginx/sites-available/postfixadmin-ssl /etc/nginx/sites-enabled/postfixadmin-ssl

  nginx -t && systemctl reload nginx
fi

# ---------------- FINAL ----------------
echo ""
echo "🎉 MAIL SERVER READY 🎉"
echo ""
echo "🌐 ACCESS LINKS"
echo "----------------------------------"
echo "📧 Webmail (Roundcube):"
echo "http://$MAIL_HOSTNAME"
echo ""
echo "📫 Admin Panel (PostfixAdmin):"
echo "http://postfix.$DOMAIN"
echo ""
echo "⚠️ FIRST TIME:"
echo "1. Open PostfixAdmin"
echo "2. Create admin user"
echo "3. Add domain"
echo "4. Create mailbox"
echo ""
echo "📌 DNS REQUIRED (BASE):"
echo "A  → $MAIL_HOSTNAME → YOUR SERVER IP"
echo "A  → postfix.$DOMAIN → YOUR SERVER IP"
echo "MX → $DOMAIN → $MAIL_HOSTNAME"
echo ""
echo "📌 AFTER ADDING A DOMAIN:"
echo "👉 DKIM is generated automatically (no manual step)"
echo ""
echo "📌 GET DNS RECORDS:"
echo "Run:"
echo "👉 mail-dns yourdomain.com"
echo ""
echo "This will output:"
echo "- SPF record"
echo "- DKIM record"
echo "- DMARC record"
echo ""
echo "⚠️ If DKIM is not ready, it will show an error"
echo ""
echo "📌 SPF (example):"
echo "v=spf1 mx a ~all"
echo ""
echo "📌 DMARC (recommended):"
echo "v=DMARC1; p=quarantine; rua=mailto:postmaster@yourdomain.com"
echo ""
echo "🔁 After DNS propagates:"
echo "Run script again to enable SSL"
echo ""