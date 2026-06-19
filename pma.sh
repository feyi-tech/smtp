#!/bin/bash

set -e

echo "==== phpMyAdmin Auto Installer (Nginx + SSL) ===="

# Prompt for domain
read -p "Enter domain for phpMyAdmin (e.g. pma.yourdomain.com): " DOMAIN
read -p "Enter email for SSL (Let's Encrypt): " EMAIL

# Update system
echo "Updating system..."
sudo apt update -y

# Install UFW if not present
if ! command -v ufw &> /dev/null; then
  echo "Installing UFW firewall..."
  sudo apt install -y ufw
fi

# Enable UFW if inactive
if sudo ufw status | grep -q "inactive"; then
  echo "Enabling UFW..."
  sudo ufw allow OpenSSH
  sudo ufw --force enable
fi

# Open required ports
echo "Opening ports 80 and 443..."
sudo ufw allow 80
sudo ufw allow 443

# Install nginx if not installed
if ! command -v nginx &> /dev/null; then
  echo "Installing Nginx..."
  sudo apt install -y nginx
else
  echo "Nginx already installed."
fi

# Install MySQL if not installed
if ! command -v mysql &> /dev/null; then
  echo "Installing MySQL Server..."
  sudo apt install -y mysql-server
else
  echo "MySQL already installed."
fi

# Install PHP and required extensions
echo "Installing PHP..."
sudo apt install -y php php-fpm php-mysql php-cli php-curl php-mbstring php-zip php-gd php-json

# Detect PHP-FPM socket
PHP_SOCK=$(find /var/run/php/ -name "php*-fpm.sock" | head -n 1)

if [ -z "$PHP_SOCK" ]; then
  echo "PHP-FPM socket not found. Restarting PHP..."
  sudo systemctl restart php*-fpm
  PHP_SOCK=$(find /var/run/php/ -name "php*-fpm.sock" | head -n 1)
fi

echo "Using PHP socket: $PHP_SOCK"

# Install phpMyAdmin
echo "Installing phpMyAdmin..."
sudo apt install -y phpmyadmin

# Ensure symlink exists
sudo ln -s /usr/share/phpmyadmin /var/www/phpmyadmin || true

# Create Nginx config
NGINX_CONF="/etc/nginx/sites-available/phpmyadmin"

echo "Creating Nginx config..."
sudo bash -c "cat > $NGINX_CONF" <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    root /var/www/phpmyadmin;
    index index.php index.html index.htm;

    location / {
        try_files \$uri \$uri/ =404;
    }

    location ~ \.php\$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:$PHP_SOCK;
    }

    location ~ /\.ht {
        deny all;
    }
}
EOF

# Enable site
sudo ln -s $NGINX_CONF /etc/nginx/sites-enabled/ || true

# Remove default site
sudo rm -f /etc/nginx/sites-enabled/default || true

# Restart nginx
echo "Restarting Nginx..."
sudo nginx -t
sudo systemctl restart nginx

# Install certbot if not installed
if ! command -v certbot &> /dev/null; then
  echo "Installing Certbot..."
  sudo apt install -y certbot python3-certbot-nginx
else
  echo "Certbot already installed."
fi

# Generate SSL
CERT_PATH="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"

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
if ping -c 1 $DOMAIN &> /dev/null; then
  if should_issue_cert; then
    echo "Issuing/renewing SSL certificate..."
    certbot --nginx -d $DOMAIN --non-interactive --agree-tos -m $EMAIL --redirect
  else
    echo "SSL certificate is still valid → skipping"
  fi
else
  echo "Skipping SSL (DNS not ready)"
fi

echo "==== DONE ===="
echo "Access phpMyAdmin at: https://$DOMAIN"