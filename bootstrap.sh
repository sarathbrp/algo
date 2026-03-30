#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# AlgoSphere Bootstrap Script
#
# One-command setup for a fresh VM (DigitalOcean, AWS, GCP, bare metal).
# Detects OS (Ubuntu/Debian, CentOS/RHEL/Fedora, Amazon Linux, Alpine)
# and installs Docker, Docker Compose, Git, then builds and starts the
# full stack: TiDB + Redis + FastAPI + Worker + React/nginx.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/psuriset/algo/main/bootstrap.sh | bash
#   # or
#   chmod +x bootstrap.sh && ./bootstrap.sh
#
# Environment variables (optional — prompted interactively if not set):
#   ALGO_REPO            Git repo URL (default: https://github.com/psuriset/algo.git)
#   ALGO_BRANCH          Branch to checkout (default: dockerize-devel)
#   ALGO_DIR             Install directory (default: /opt/algosphere)
#   APCA_API_KEY_ID      Alpaca paper trading API key
#   APCA_API_SECRET_KEY  Alpaca paper trading API secret
#   FRONTEND_PORT        Frontend port (default: 80)
#   API_PORT             API port (default: 8000)
# ---------------------------------------------------------------------------
set -euo pipefail

# ---- Colors ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log()  { echo -e "${GREEN}[algosphere]${NC} $*"; }
warn() { echo -e "${YELLOW}[algosphere]${NC} $*"; }
err()  { echo -e "${RED}[algosphere]${NC} $*" >&2; }

# ---------------------------------------------------------------------------
# 1. Detect OS
# ---------------------------------------------------------------------------
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_ID="${ID:-unknown}"
        OS_ID_LIKE="${ID_LIKE:-}"
        OS_VERSION="${VERSION_ID:-}"
    elif [ -f /etc/redhat-release ]; then
        OS_ID="rhel"
        OS_ID_LIKE="rhel"
    else
        OS_ID="unknown"
        OS_ID_LIKE=""
    fi

    # Normalize to package manager family
    case "$OS_ID" in
        ubuntu|debian|pop|linuxmint)
            PKG_FAMILY="debian"
            ;;
        centos|rhel|rocky|almalinux|ol)
            PKG_FAMILY="rhel"
            ;;
        fedora)
            PKG_FAMILY="fedora"
            ;;
        amzn)
            PKG_FAMILY="amzn"
            ;;
        alpine)
            PKG_FAMILY="alpine"
            ;;
        *)
            # Try ID_LIKE as fallback
            case "$OS_ID_LIKE" in
                *debian*|*ubuntu*) PKG_FAMILY="debian" ;;
                *rhel*|*centos*|*fedora*) PKG_FAMILY="rhel" ;;
                *) PKG_FAMILY="unknown" ;;
            esac
            ;;
    esac

    log "Detected OS: ${CYAN}${OS_ID}${NC} (family: ${PKG_FAMILY})"
}

# ---------------------------------------------------------------------------
# 2. Install system packages
# ---------------------------------------------------------------------------
install_prerequisites() {
    log "Installing prerequisites (curl, git, ca-certificates)..."

    case "$PKG_FAMILY" in
        debian)
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -qq
            apt-get install -y -qq curl git ca-certificates gnupg lsb-release >/dev/null
            ;;
        rhel)
            yum install -y -q curl git ca-certificates yum-utils >/dev/null
            ;;
        fedora)
            dnf install -y -q curl git ca-certificates dnf-plugins-core >/dev/null
            ;;
        amzn)
            yum install -y -q curl git ca-certificates >/dev/null
            ;;
        alpine)
            apk update >/dev/null
            apk add --no-cache curl git ca-certificates bash >/dev/null
            ;;
        *)
            err "Unsupported OS family: $PKG_FAMILY"
            err "Please install Docker manually, then re-run this script."
            exit 1
            ;;
    esac
}

# ---------------------------------------------------------------------------
# 3. Install Docker
# ---------------------------------------------------------------------------
install_docker() {
    if command -v docker &>/dev/null; then
        log "Docker already installed: $(docker --version)"
        return
    fi

    log "Installing Docker..."

    case "$PKG_FAMILY" in
        debian)
            # Docker official GPG key
            install -m 0755 -d /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/${OS_ID}/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null
            chmod a+r /etc/apt/keyrings/docker.gpg
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${OS_ID} $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list
            apt-get update -qq
            apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
            ;;
        rhel)
            yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo >/dev/null 2>&1
            yum install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
            ;;
        fedora)
            dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo >/dev/null 2>&1
            dnf install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
            ;;
        amzn)
            # Amazon Linux 2023 uses dnf; AL2 uses yum + amazon-linux-extras
            if command -v dnf &>/dev/null; then
                dnf install -y -q docker >/dev/null
            else
                amazon-linux-extras install docker -y >/dev/null 2>&1 || yum install -y -q docker >/dev/null
            fi
            ;;
        alpine)
            apk add --no-cache docker docker-cli-compose >/dev/null
            ;;
    esac

    # Start and enable Docker
    if command -v systemctl &>/dev/null; then
        systemctl start docker
        systemctl enable docker
    elif command -v rc-service &>/dev/null; then
        rc-service docker start
        rc-update add docker default
    fi

    log "Docker installed: $(docker --version)"
}

# ---------------------------------------------------------------------------
# 4. Ensure Docker Compose (v2 plugin)
# ---------------------------------------------------------------------------
ensure_compose() {
    if docker compose version &>/dev/null; then
        log "Docker Compose available: $(docker compose version --short)"
        return
    fi

    # Fallback: install compose plugin manually
    log "Installing Docker Compose plugin..."
    COMPOSE_VERSION=$(curl -fsSL https://api.github.com/repos/docker/compose/releases/latest | grep '"tag_name"' | head -1 | cut -d'"' -f4)
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  ARCH="x86_64" ;;
        aarch64|arm64) ARCH="aarch64" ;;
        *) err "Unsupported architecture: $ARCH"; exit 1 ;;
    esac
    COMPOSE_URL="https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${ARCH}"
    mkdir -p /usr/local/lib/docker/cli-plugins
    curl -fsSL "$COMPOSE_URL" -o /usr/local/lib/docker/cli-plugins/docker-compose
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
    log "Docker Compose installed: $(docker compose version --short)"
}

# ---------------------------------------------------------------------------
# 5. Clone or update the repository
# ---------------------------------------------------------------------------
setup_repo() {
    ALGO_REPO="${ALGO_REPO:-https://github.com/psuriset/algo.git}"
    ALGO_BRANCH="${ALGO_BRANCH:-dockerize-devel}"
    ALGO_DIR="${ALGO_DIR:-/opt/algosphere}"

    if [ -d "$ALGO_DIR/.git" ]; then
        log "Repository exists at $ALGO_DIR — pulling latest..."
        cd "$ALGO_DIR"
        git fetch origin
        git checkout "$ALGO_BRANCH"
        git pull origin "$ALGO_BRANCH" || true
    else
        log "Cloning $ALGO_REPO (branch: $ALGO_BRANCH) into $ALGO_DIR..."
        git clone --branch "$ALGO_BRANCH" "$ALGO_REPO" "$ALGO_DIR"
        cd "$ALGO_DIR"
    fi

    log "Repository ready at ${CYAN}$ALGO_DIR${NC}"
}

# ---------------------------------------------------------------------------
# 6. Configure environment
# ---------------------------------------------------------------------------
setup_env() {
    cd "$ALGO_DIR"

    if [ -f .env ]; then
        warn ".env already exists — keeping existing configuration."
        return
    fi

    log "Creating .env from template..."
    cp .env.example .env

    # Generate a random JWT secret
    JWT_SECRET=$(openssl rand -hex 32 2>/dev/null || head -c 64 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 64)
    sed -i "s/^JWT_SECRET=.*/JWT_SECRET=${JWT_SECRET}/" .env

    # Set ports
    FRONTEND_PORT="${FRONTEND_PORT:-80}"
    API_PORT="${API_PORT:-8000}"
    sed -i "s/^FRONTEND_PORT=.*/FRONTEND_PORT=${FRONTEND_PORT}/" .env
    sed -i "s/^API_PORT=.*/API_PORT=${API_PORT}/" .env

    # Alpaca keys (from env or prompt)
    if [ -n "${APCA_API_KEY_ID:-}" ]; then
        sed -i "s/^APCA_API_KEY_ID=.*/APCA_API_KEY_ID=${APCA_API_KEY_ID}/" .env
        sed -i "s/^APCA_API_SECRET_KEY=.*/APCA_API_SECRET_KEY=${APCA_API_SECRET_KEY:-}/" .env
        log "Alpaca keys set from environment."
    elif [ -t 0 ]; then
        # Interactive terminal — prompt
        echo ""
        echo -e "${CYAN}Alpaca API keys (paper trading). Leave blank to configure later.${NC}"
        read -rp "  APCA_API_KEY_ID: " key_id
        read -rp "  APCA_API_SECRET_KEY: " secret_key
        if [ -n "$key_id" ]; then
            sed -i "s/^APCA_API_KEY_ID=.*/APCA_API_KEY_ID=${key_id}/" .env
            sed -i "s/^APCA_API_SECRET_KEY=.*/APCA_API_SECRET_KEY=${secret_key}/" .env
        fi
    else
        warn "No Alpaca keys provided. Set them later in $ALGO_DIR/.env"
    fi

    # Add Redis port if not in template
    if ! grep -q "^REDIS_PORT" .env; then
        echo "REDIS_PORT=6379" >> .env
    fi

    log ".env configured."
}

# ---------------------------------------------------------------------------
# 7. Open firewall ports
# ---------------------------------------------------------------------------
setup_firewall() {
    FRONTEND_PORT="${FRONTEND_PORT:-80}"
    API_PORT="${API_PORT:-8000}"

    if command -v ufw &>/dev/null; then
        log "Configuring UFW firewall..."
        ufw allow "$FRONTEND_PORT"/tcp >/dev/null 2>&1 || true
        ufw allow "$API_PORT"/tcp >/dev/null 2>&1 || true
        ufw allow 22/tcp >/dev/null 2>&1 || true
    elif command -v firewall-cmd &>/dev/null; then
        log "Configuring firewalld..."
        firewall-cmd --permanent --add-port="$FRONTEND_PORT"/tcp >/dev/null 2>&1 || true
        firewall-cmd --permanent --add-port="$API_PORT"/tcp >/dev/null 2>&1 || true
        firewall-cmd --reload >/dev/null 2>&1 || true
    fi
}

# ---------------------------------------------------------------------------
# 8. Build and launch
# ---------------------------------------------------------------------------
build_and_launch() {
    cd "$ALGO_DIR"

    log "Building Docker images (this may take a few minutes on first run)..."
    docker compose build 2>&1 | tail -5

    log "Starting all services..."
    docker compose up -d 2>&1

    # Wait for health checks
    log "Waiting for services to become healthy..."
    local retries=0
    while [ $retries -lt 60 ]; do
        if docker compose exec -T api python -c "print('ok')" >/dev/null 2>&1; then
            break
        fi
        retries=$((retries + 1))
        sleep 2
    done

    if [ $retries -ge 60 ]; then
        err "Timed out waiting for services. Check logs with: docker compose logs"
        exit 1
    fi

    log "All services are up."
}

# ---------------------------------------------------------------------------
# 9. Verify
# ---------------------------------------------------------------------------
verify() {
    cd "$ALGO_DIR"
    FRONTEND_PORT="${FRONTEND_PORT:-80}"
    API_PORT="${API_PORT:-8000}"

    echo ""
    log "Running health checks..."

    # API health
    if curl -fsSL "http://localhost:${API_PORT}/healthz" >/dev/null 2>&1; then
        log "  API:      ${GREEN}OK${NC} (port ${API_PORT})"
    else
        warn "  API:      NOT READY (port ${API_PORT})"
    fi

    # Frontend
    if curl -fsSL "http://localhost:${FRONTEND_PORT}" >/dev/null 2>&1; then
        log "  Frontend: ${GREEN}OK${NC} (port ${FRONTEND_PORT})"
    else
        warn "  Frontend: NOT READY (port ${FRONTEND_PORT})"
    fi

    # Container status
    echo ""
    docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
}

# ---------------------------------------------------------------------------
# 10. Print summary
# ---------------------------------------------------------------------------
print_summary() {
    FRONTEND_PORT="${FRONTEND_PORT:-80}"
    API_PORT="${API_PORT:-8000}"

    # Get public IP
    PUBLIC_IP=$(curl -fsSL https://ifconfig.me 2>/dev/null || curl -fsSL https://api.ipify.org 2>/dev/null || echo "YOUR_SERVER_IP")

    echo ""
    echo -e "${GREEN}================================================================${NC}"
    echo -e "${GREEN}  AlgoSphere is running!${NC}"
    echo -e "${GREEN}================================================================${NC}"
    echo ""
    echo -e "  Dashboard:  ${CYAN}http://${PUBLIC_IP}:${FRONTEND_PORT}${NC}"
    echo -e "  API:        ${CYAN}http://${PUBLIC_IP}:${API_PORT}${NC}"
    echo -e "  API docs:   ${CYAN}http://${PUBLIC_IP}:${API_PORT}/docs${NC}"
    echo ""
    echo -e "  Install dir:  $ALGO_DIR"
    echo -e "  Config:       $ALGO_DIR/.env"
    echo ""
    echo -e "  ${YELLOW}Quick start:${NC}"
    echo -e "    1. Open the dashboard URL above"
    echo -e "    2. Register an account"
    echo -e "    3. Go through onboarding (enter Alpaca API keys)"
    echo -e "    4. The trading worker will pick up your account automatically"
    echo ""
    echo -e "  ${YELLOW}Useful commands:${NC}"
    echo -e "    cd $ALGO_DIR"
    echo -e "    docker compose logs -f worker    # watch trading activity"
    echo -e "    docker compose logs -f api       # watch API requests"
    echo -e "    docker compose restart worker    # restart trading loop"
    echo -e "    docker compose down              # stop everything"
    echo -e "    docker compose up -d             # start everything"
    echo ""
    echo -e "  ${YELLOW}Update to latest:${NC}"
    echo -e "    cd $ALGO_DIR && git pull && docker compose build && docker compose up -d"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  AlgoSphere Bootstrap${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""

    # Must be root or sudo
    if [ "$(id -u)" -ne 0 ]; then
        err "This script must be run as root (or with sudo)."
        exit 1
    fi

    detect_os
    install_prerequisites
    install_docker
    ensure_compose
    setup_repo
    setup_env
    setup_firewall
    build_and_launch
    verify
    print_summary
}

main "$@"
