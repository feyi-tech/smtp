# MailStack

Docker-first Postfix + Dovecot mail server setup with a tokenized web setup page, Roundcube webmail, PostfixAdmin, OpenDKIM, and copyable DNS guidance.

## Why Docker

Linux mail setup differs sharply between distros. This project avoids distro-specific package and service layouts by running the mail stack in one consistent container. The host only needs Docker with Compose support.

## Quick Start

Download and run:

```bash
wget https://raw.githubusercontent.com/feyi-tech/smtp/main/mailstack.sh
chmod +x mailstack.sh
./mailstack.sh install
```

That same command works whether Docker already exists or not. If Docker is missing, the script installs and starts it automatically:

- macOS: installs and starts Docker Desktop.
- Linux: installs Docker Engine and the Docker Compose plugin, then starts Docker.

If only `mailstack.sh` was downloaded, it automatically downloads the rest of the MailStack project from GitHub before building the containers.

The command prints a passwordless setup URL like:

```text
http://SERVER_IP:SETUP_PORT/setup/TOKEN
```

The setup page uses port `8080` when available. If `8080` is already in use, MailStack automatically chooses the next free port from `8081` to `8099` and prints the correct URL.

Keep that URL private. It configures the mail stack.

## Ports

Open these host ports:

- `25` SMTP
- `465` SMTPS
- `587` Submission
- `143` IMAP
- `993` IMAPS
- `80` Webmail/PostfixAdmin
- `443` HTTPS for Roundcube and PostfixAdmin
- `8080-8099` Tokenized setup UI, using the first available port

## Setup Page

The generated setup URL lets the server admin provide or update:

- Mail hostname
- Roundcube website domain
- PostfixAdmin website domain
- PostfixAdmin admin email
- PostfixAdmin admin password

Database passwords are generated automatically and stored inside the container state. The admin does not need to know or enter them. The same setup UI includes a password reset page for the PostfixAdmin admin account.

## Admin Apps

After setup:

- Roundcube: `https://mail.example.com/`
- PostfixAdmin: `https://postfix.example.com/`
- Setup UI DNS records: `http://SERVER_IP:SETUP_PORT/setup/TOKEN/dns`
- Setup UI connection details: `http://SERVER_IP:SETUP_PORT/setup/TOKEN/connection`
- PostfixAdmin DNS page: `https://postfix.example.com/mailstack_dns.php`
- PostfixAdmin connection page: `https://postfix.example.com/mailstack_connection.php`

## Email Client Settings

MailStack shows copyable connection details in both the setup UI and PostfixAdmin:

- SMTP submission: `mail.example.com`, port `587`, STARTTLS
- SMTP over TLS: `mail.example.com`, port `465`, implicit TLS
- IMAP: `mail.example.com`, port `143`, STARTTLS
- IMAPS: `mail.example.com`, port `993`, implicit TLS
- Username format: full mailbox address, such as `user@example.com`
- Password: the mailbox password created in PostfixAdmin

## HTTPS

MailStack enables HTTPS for both Roundcube and PostfixAdmin. During setup it first creates a self-signed fallback certificate so port `443` works immediately, then it tries to issue a Let's Encrypt certificate for the mail, Roundcube, and PostfixAdmin hostnames. Let's Encrypt issuance requires the hostnames to point to the server and inbound port `80` to be reachable. If issuance fails, the sites remain available over HTTPS with the self-signed fallback certificate.

## DNS

For each active PostfixAdmin domain, MailStack shows:

- `A` records for the mail, Roundcube, and PostfixAdmin hostnames
- `MX` record for the domain
- `TXT` SPF record
- `TXT` DKIM record
- `TXT` DMARC record

DKIM keys are generated automatically when domains are added in PostfixAdmin.

## Commands

```bash
./mailstack.sh install       # install Docker if needed, build, and start
./mailstack.sh url           # print setup URL
./mailstack.sh status        # show containers
./mailstack.sh logs          # follow mail container logs
./mailstack.sh down          # stop containers
./mailstack.sh destroy --yes # delete containers and Docker volumes
```

## Docker Notes

On macOS, Docker Desktop may open a first-run setup screen. Finish that screen once, then rerun `./mailstack.sh install` if the script is waiting for Docker.

On Linux, the installer may add your user to the `docker` group. That group change normally applies after logging out and back in, so the script uses `sudo` automatically when needed during the current session.

## Limits

The Docker image gives a distro-independent host install path, but mail delivery still depends on DNS, PTR/rDNS, port 25 availability, IP reputation, and provider policy.
