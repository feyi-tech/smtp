FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
  && echo "postfix postfix/mailname string mail.local" | debconf-set-selections \
  && echo "postfix postfix/main_mailer_type string Internet Site" | debconf-set-selections \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    dovecot-core \
    dovecot-imapd \
    dovecot-lmtpd \
    dovecot-mysql \
    mariadb-client \
    mariadb-server \
    nginx \
    opendkim \
    opendkim-tools \
    openssl \
    php-cli \
    php-common \
    php-curl \
    php-fpm \
    php-gd \
    php-imap \
    php-intl \
    php-mbstring \
    php-mysql \
    php-sqlite3 \
    php-xml \
    php-zip \
    postfix \
    postfix-mysql \
    python3 \
    ssl-cert \
    supervisor \
    unzip \
    wget \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

COPY docker/rootfs/ /

RUN chmod +x /usr/local/bin/mailstack-* /opt/mailstack/setup/*.py /opt/mailstack/postfixadmin/*.py \
  && mkdir -p /data/state /run/php /var/vmail /var/log/supervisor \
  && chown -R www-data:www-data /var/www || true

EXPOSE 25 465 587 143 993 80 443 8080

ENTRYPOINT ["/usr/local/bin/mailstack-entrypoint"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/mailstack.conf"]
