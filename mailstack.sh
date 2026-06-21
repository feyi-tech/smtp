#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME=${PROJECT_NAME:-mailstack}
COMPOSE_FILE=${COMPOSE_FILE:-compose.yaml}
DOCKER_WAIT_SECONDS=${DOCKER_WAIT_SECONDS:-180}
MAILSTACK_REPO_URL=${MAILSTACK_REPO_URL:-https://github.com/feyi-tech/smtp/archive/refs/heads/main.tar.gz}
MAILSTACK_STATE_DIR=${MAILSTACK_STATE_DIR:-.mailstack}
MAILSTACK_ENV_FILE=${MAILSTACK_ENV_FILE:-$MAILSTACK_STATE_DIR/setup.env}
MAILSTACK_SETUP_PORT_START=${MAILSTACK_SETUP_PORT_START:-8080}
MAILSTACK_SETUP_PORT_END=${MAILSTACK_SETUP_PORT_END:-8099}

log() {
  printf '\n========== %s ==========\n' "$1"
}

load_env_file() {
  if [ -f "$MAILSTACK_ENV_FILE" ]; then
    # shellcheck disable=SC1090
    . "$MAILSTACK_ENV_FILE"
    if [ -n "${MAILSTACK_SETUP_PORT:-}" ]; then
      export MAILSTACK_SETUP_PORT
    fi
    if [ -n "${MAILSTACK_PUBLIC_HOST:-}" ]; then
      export MAILSTACK_PUBLIC_HOST
    fi
  fi
}

compose() {
  load_env_file
  if compose_available_modern; then
    docker_cmd compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    if docker-compose version >/dev/null 2>&1; then
      docker-compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
    else
      as_root docker-compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
    fi
  else
    echo "Docker Compose is required. Install Docker Engine with the Compose plugin first."
    exit 1
  fi
}

compose_available_modern() {
  docker_cmd compose version >/dev/null 2>&1
}

compose_available() {
  compose_available_modern || command -v docker-compose >/dev/null 2>&1
}

as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "This step needs root privileges, but sudo is not installed. Run as root or install sudo."
    exit 1
  fi
}

download_file() {
  URL=$1
  TARGET=$2

  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$URL" -o "$TARGET"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$TARGET" "$URL"
  else
    echo "curl or wget is required to download MailStack/Docker files."
    exit 1
  fi
}

ensure_project_files() {
  if [ -f "$COMPOSE_FILE" ] && [ -f "Dockerfile" ] && [ -d "docker/rootfs" ]; then
    return 0
  fi

  echo "MailStack project files are missing. Downloading the full project from GitHub..."

  if ! command -v tar >/dev/null 2>&1; then
    echo "tar is required to unpack the MailStack project archive."
    exit 1
  fi

  TMP_DIR=$(mktemp -d /tmp/mailstack-project.XXXXXX)
  ARCHIVE="$TMP_DIR/mailstack.tar.gz"
  download_file "$MAILSTACK_REPO_URL" "$ARCHIVE"
  tar -xzf "$ARCHIVE" -C "$TMP_DIR"

  PROJECT_DIR=$(find "$TMP_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  if [ -z "$PROJECT_DIR" ] || [ ! -f "$PROJECT_DIR/mailstack.sh" ]; then
    echo "Downloaded archive did not contain the MailStack project."
    rm -rf "$TMP_DIR"
    exit 1
  fi

  cp -R "$PROJECT_DIR"/. .
  chmod +x mailstack.sh
  rm -rf "$TMP_DIR"
  echo "MailStack project files are ready."
}

port_is_available() {
  PORT=$1

  if command -v nc >/dev/null 2>&1; then
    if nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1; then
      return 1
    fi
    return 0
  fi

  if command -v ss >/dev/null 2>&1; then
    if ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$PORT$"; then
      return 1
    fi
    return 0
  fi

  if command -v lsof >/dev/null 2>&1; then
    if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      return 1
    fi
    return 0
  fi

  return 0
}

save_setup_env() {
  mkdir -p "$MAILSTACK_STATE_DIR"
  {
    if [ -n "${MAILSTACK_SETUP_PORT:-}" ]; then
      echo "MAILSTACK_SETUP_PORT=$MAILSTACK_SETUP_PORT"
    fi
    if [ -n "${MAILSTACK_PUBLIC_HOST:-}" ]; then
      echo "MAILSTACK_PUBLIC_HOST=$MAILSTACK_PUBLIC_HOST"
    fi
  } > "$MAILSTACK_ENV_FILE"
}

save_setup_port() {
  PORT=$1
  export MAILSTACK_SETUP_PORT=$PORT
  save_setup_env
}

save_public_host() {
  HOST=$1
  if [ -n "$HOST" ]; then
    export MAILSTACK_PUBLIC_HOST=$HOST
    save_setup_env
  fi
}

running_setup_port() {
  if ! docker_bin >/dev/null 2>&1 || ! compose_available; then
    return 1
  fi

  PORT_LINE=$(compose port mail 8080 2>/dev/null | head -n 1 || true)
  if [ -z "$PORT_LINE" ]; then
    return 1
  fi

  PORT=${PORT_LINE##*:}
  case "$PORT" in
    ''|*[!0-9]*) return 1 ;;
  esac

  echo "$PORT"
}

sync_setup_port_from_running_container() {
  RUNNING_PORT=$(running_setup_port || true)
  if [ -n "$RUNNING_PORT" ]; then
    save_setup_port "$RUNNING_PORT"
    return 0
  fi

  return 1
}

choose_setup_port() {
  if [ -n "${MAILSTACK_SETUP_PORT:-}" ]; then
    export MAILSTACK_SETUP_PORT
    return 0
  fi

  load_env_file
  if [ -n "${MAILSTACK_SETUP_PORT:-}" ]; then
    export MAILSTACK_SETUP_PORT
    return 0
  fi

  PORT=$MAILSTACK_SETUP_PORT_START
  while [ "$PORT" -le "$MAILSTACK_SETUP_PORT_END" ]; do
    if port_is_available "$PORT"; then
      save_setup_port "$PORT"
      echo "Using setup UI port $MAILSTACK_SETUP_PORT."
      return 0
    fi
    PORT=$((PORT + 1))
  done

  echo "No free setup UI port found between $MAILSTACK_SETUP_PORT_START and $MAILSTACK_SETUP_PORT_END."
  echo "Free one of those ports or set MAILSTACK_SETUP_PORT before running install."
  exit 1
}

docker_bin() {
  if command -v docker >/dev/null 2>&1; then
    command -v docker
    return 0
  fi

  if [ -x "/Applications/Docker.app/Contents/Resources/bin/docker" ]; then
    echo "/Applications/Docker.app/Contents/Resources/bin/docker"
    return 0
  fi

  return 1
}

docker_cmd() {
  BIN=$(docker_bin) || return 127

  if "$BIN" info >/dev/null 2>&1; then
    "$BIN" "$@"
  elif [ "$(uname -s)" = "Darwin" ]; then
    "$BIN" "$@"
  else
    as_root "$BIN" "$@"
  fi
}

docker_is_running() {
  docker_cmd info >/dev/null 2>&1
}

start_docker_service() {
  case "$(uname -s)" in
    Darwin)
      if [ -d "/Applications/Docker.app" ]; then
        echo "Starting Docker Desktop..."
        open -a Docker || true
      fi
      ;;
    Linux)
      if command -v systemctl >/dev/null 2>&1; then
        as_root systemctl enable --now docker >/dev/null 2>&1 || true
      elif command -v service >/dev/null 2>&1; then
        as_root service docker start >/dev/null 2>&1 || true
      fi
      ;;
  esac
}

wait_for_docker() {
  start_docker_service
  echo "Waiting for Docker to be ready..."

  ELAPSED=0
  while [ "$ELAPSED" -lt "$DOCKER_WAIT_SECONDS" ]; do
    if docker_is_running; then
      echo "Docker is ready."
      return 0
    fi
    sleep 3
    ELAPSED=$((ELAPSED + 3))
  done

  echo "Docker is installed, but it is not running yet."
  if [ "$(uname -s)" = "Darwin" ]; then
    echo "Open Docker Desktop from Applications, finish any first-run prompts, then rerun ./mailstack.sh install."
  else
    echo "Try: sudo systemctl start docker"
  fi
  exit 1
}

install_linux_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    as_root apt-get update
    as_root apt-get install -y "$@"
  elif command -v dnf >/dev/null 2>&1; then
    as_root dnf install -y "$@"
  elif command -v yum >/dev/null 2>&1; then
    as_root yum install -y "$@"
  elif command -v zypper >/dev/null 2>&1; then
    as_root zypper --non-interactive install "$@"
  elif command -v apk >/dev/null 2>&1; then
    as_root apk add "$@"
  elif command -v pacman >/dev/null 2>&1; then
    as_root pacman -Sy --noconfirm "$@"
  else
    echo "No supported package manager found. Install curl and Docker manually, then rerun this script."
    exit 1
  fi
}

ensure_curl() {
  if command -v curl >/dev/null 2>&1; then
    return 0
  fi

  echo "curl is required to install Docker."
  install_linux_packages curl ca-certificates
}

install_docker_linux() {
  echo "Docker is not installed."
  echo "Installing Docker Engine and the Compose plugin automatically."
  echo "Root/sudo privileges may be requested by your system."

  ensure_curl
  TMP_SCRIPT=$(mktemp /tmp/mailstack-get-docker.XXXXXX)
  curl -fsSL https://get.docker.com -o "$TMP_SCRIPT"
  as_root sh "$TMP_SCRIPT"
  rm -f "$TMP_SCRIPT"

  if command -v groupadd >/dev/null 2>&1; then
    as_root groupadd docker >/dev/null 2>&1 || true
  fi

  if [ -n "${SUDO_USER:-}" ] && command -v usermod >/dev/null 2>&1; then
    as_root usermod -aG docker "$SUDO_USER" >/dev/null 2>&1 || true
    echo "User $SUDO_USER was added to the docker group. This script will use sudo as needed until the next login."
  elif [ "$(id -u)" -ne 0 ] && command -v usermod >/dev/null 2>&1; then
    as_root usermod -aG docker "$USER" >/dev/null 2>&1 || true
    echo "User $USER was added to the docker group. This script will use sudo as needed until the next login."
  fi

  wait_for_docker
}

install_docker_macos_with_brew() {
  echo "Installing Docker Desktop with Homebrew..."
  brew install --cask docker
}

install_docker_macos_with_dmg() {
  ARCH=$(uname -m)
  case "$ARCH" in
    arm64) DOCKER_DMG_URL="https://desktop.docker.com/mac/main/arm64/Docker.dmg" ;;
    x86_64) DOCKER_DMG_URL="https://desktop.docker.com/mac/main/amd64/Docker.dmg" ;;
    *)
      echo "Unsupported Mac architecture: $ARCH"
      exit 1
      ;;
  esac

  TMP_DIR=$(mktemp -d /tmp/mailstack-docker-desktop.XXXXXX)
  DMG="$TMP_DIR/Docker.dmg"
  curl -L "$DOCKER_DMG_URL" -o "$DMG"
  hdiutil attach "$DMG" -nobrowse -quiet
  as_root cp -R "/Volumes/Docker/Docker.app" /Applications/
  hdiutil detach "/Volumes/Docker" -quiet
  rm -rf "$TMP_DIR"
}

install_docker_macos() {
  echo "Docker is not installed."
  echo "Installing Docker Desktop automatically."
  echo "Docker Desktop may require accepting Docker's license and completing a first-run setup screen."

  if command -v brew >/dev/null 2>&1; then
    install_docker_macos_with_brew
  else
    if ! command -v curl >/dev/null 2>&1; then
      echo "curl is required to download Docker Desktop. Install Homebrew or curl, then rerun this script."
      exit 1
    fi
    install_docker_macos_with_dmg
  fi

  wait_for_docker
}

install_docker() {
  case "$(uname -s)" in
    Linux) install_docker_linux ;;
    Darwin) install_docker_macos ;;
    *)
      echo "Unsupported system: $(uname -s). Install Docker manually, then rerun this script."
      exit 1
      ;;
  esac
}

ensure_docker() {
  if ! docker_bin >/dev/null 2>&1; then
    install_docker
  else
    wait_for_docker
  fi

  if ! compose_available; then
    echo "Docker is installed, but Docker Compose is not available."
    if [ "$(uname -s)" = "Linux" ]; then
      echo "Trying to install the Docker Compose plugin..."
      if command -v apt-get >/dev/null 2>&1; then
        as_root apt-get update
        as_root apt-get install -y docker-compose-plugin
      elif command -v dnf >/dev/null 2>&1; then
        as_root dnf install -y docker-compose-plugin
      elif command -v yum >/dev/null 2>&1; then
        as_root yum install -y docker-compose-plugin
      else
        echo "Install the Docker Compose plugin manually, then rerun this script."
        exit 1
      fi
    else
      echo "Start or update Docker Desktop, then rerun this script."
      exit 1
    fi

    if ! compose_available; then
      echo "Docker Compose is still not available after installation."
      echo "Install the Docker Compose plugin manually, then rerun ./mailstack.sh install."
      exit 1
    fi
  fi
}

detect_host() {
  if [ -n "${MAILSTACK_PUBLIC_HOST:-}" ]; then
    echo "$MAILSTACK_PUBLIC_HOST"
    return
  fi

  if command -v hostname >/dev/null 2>&1; then
    HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
    if [ -n "${HOST_IP:-}" ]; then
      echo "$HOST_IP"
      return
    fi
  fi

  if command -v curl >/dev/null 2>&1; then
    PUBLIC_IP=$(curl -fsS --max-time 3 https://ifconfig.me 2>/dev/null || true)
    if [ -n "${PUBLIC_IP:-}" ]; then
      echo "$PUBLIC_IP"
      return
    fi
  fi

  echo "127.0.0.1"
}

print_setup_url() {
  sync_setup_port_from_running_container || choose_setup_port
  HOST=$(detect_host)
  TOKEN=$(compose exec -T mail mailstack-token 2>/dev/null || true)

  if [ -z "${TOKEN:-}" ]; then
    echo "Setup token is not ready yet. Check logs with: ./mailstack.sh logs"
    exit 1
  fi

  echo ""
  echo "MailStack setup URL:"
  echo "http://${HOST}:${MAILSTACK_SETUP_PORT}/setup/${TOKEN}"
  echo ""
  echo "Keep this URL private. It is passwordless and controls the mail-stack setup."
}

case "${1:-up}" in
  up|install)
    ensure_project_files
    choose_setup_port
    save_public_host "$(detect_host)"
    ensure_docker
    log "Building and starting MailStack"
    compose up -d --build
    log "Setup link"
    print_setup_url
    ;;

  url)
    ensure_docker
    print_setup_url
    ;;

  status)
    ensure_docker
    compose ps
    ;;

  logs)
    ensure_docker
    compose logs -f --tail=200 "${2:-mail}"
    ;;

  down)
    ensure_docker
    compose down
    ;;

  destroy)
    ensure_docker
    if [ "${2:-}" != "--yes" ]; then
      echo "This deletes MailStack containers and Docker volumes. Rerun: ./mailstack.sh destroy --yes"
      exit 1
    fi
    compose down -v
    ;;

  *)
    echo "Usage: ./mailstack.sh [install|up|url|status|logs|down|destroy --yes]"
    exit 1
    ;;
esac
