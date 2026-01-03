#!/bin/bash
# =============================================================================
# Jetson Edge AI Point - Installation Script
# =============================================================================
# Target: NVIDIA Jetson AGX Xavier 32GB (Linux 5.15.148-tegra aarch64)
# Services: Ollama (LLM), Whisper (STT), Watchtower (auto-update)
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

check_architecture() {
    local arch=$(uname -m)
    if [[ "$arch" != "aarch64" ]]; then
        log_error "This script is designed for ARM64 (aarch64). Detected: $arch"
        exit 1
    fi
    log_success "Architecture check passed: $arch"
}

check_jetson() {
    if [[ -f /etc/nv_tegra_release ]]; then
        log_success "Jetson platform detected"
        cat /etc/nv_tegra_release
    else
        log_warn "Jetson platform not detected. Proceeding anyway..."
    fi
}

# -----------------------------------------------------------------------------
# Docker Installation (Jetson-compatible)
# -----------------------------------------------------------------------------

install_docker() {
    if command -v docker &> /dev/null; then
        log_info "Docker already installed: $(docker --version)"
    else
        log_info "Installing Docker..."
        
        # Install prerequisites
        apt-get update
        apt-get install -y \
            apt-transport-https \
            ca-certificates \
            curl \
            gnupg \
            lsb-release

        # Add Docker GPG key
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

        # Add Docker repository (arm64)
        echo \
            "deb [arch=arm64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu \
            $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

        # Install Docker
        apt-get update
        apt-get install -y docker-ce docker-ce-cli containerd.io

        log_success "Docker installed successfully"
    fi

    # Enable and start Docker
    systemctl enable docker
    systemctl start docker
}

install_docker_compose() {
    if command -v docker-compose &> /dev/null || docker compose version &> /dev/null; then
        log_info "Docker Compose already installed"
    else
        log_info "Installing Docker Compose..."
        
        # Install Docker Compose plugin
        apt-get update
        apt-get install -y docker-compose-plugin
        
        # Create symlink for docker-compose command
        if [[ ! -f /usr/local/bin/docker-compose ]]; then
            ln -s /usr/libexec/docker/cli-plugins/docker-compose /usr/local/bin/docker-compose 2>/dev/null || true
        fi
        
        log_success "Docker Compose installed successfully"
    fi
}

# -----------------------------------------------------------------------------
# NVIDIA Container Runtime (Jetson)
# -----------------------------------------------------------------------------

install_nvidia_runtime() {
    if docker info 2>/dev/null | grep -q "nvidia"; then
        log_info "NVIDIA container runtime already configured"
        return
    fi

    log_info "Configuring NVIDIA container runtime for Jetson..."

    # Install nvidia-container-toolkit if not present
    if ! dpkg -l | grep -q nvidia-container-toolkit; then
        # Add NVIDIA container toolkit repository
        distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
        curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
        curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
            sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
            tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

        apt-get update
        apt-get install -y nvidia-container-toolkit
    fi

    # Configure Docker to use nvidia runtime
    nvidia-ctk runtime configure --runtime=docker

    # Set nvidia as default runtime for Jetson
    if [[ -f /etc/docker/daemon.json ]]; then
        # Backup existing config
        cp /etc/docker/daemon.json /etc/docker/daemon.json.bak
    fi

    cat > /etc/docker/daemon.json <<EOF
{
    "runtimes": {
        "nvidia": {
            "path": "nvidia-container-runtime",
            "runtimeArgs": []
        }
    },
    "default-runtime": "nvidia"
}
EOF

    # Restart Docker to apply changes
    systemctl restart docker

    log_success "NVIDIA container runtime configured"
}

# -----------------------------------------------------------------------------
# Environment Setup
# -----------------------------------------------------------------------------

setup_environment() {
    local env_file="$SCRIPT_DIR/.env"
    local template_file="$SCRIPT_DIR/.env.template"

    if [[ -f "$env_file" ]]; then
        log_info ".env file already exists"
    else
        if [[ -f "$template_file" ]]; then
            cp "$template_file" "$env_file"
            log_warn ".env file created from template. Please edit it with your configuration!"
            log_warn "Required: GOOGLE_API_KEY and GOOGLE_CX_ID for search functionality"
        else
            log_error ".env.template not found!"
            exit 1
        fi
    fi

    # Source environment variables
    set -a
    source "$env_file"
    set +a

    # Validate required variables
    if [[ -z "$HOST_INTERFACE" ]]; then
        log_error "HOST_INTERFACE not set in .env"
        exit 1
    fi

    # Check if interface exists
    if ! ip link show "$HOST_INTERFACE" &> /dev/null; then
        log_error "Network interface $HOST_INTERFACE does not exist"
        log_info "Available interfaces:"
        ip link show | grep -E "^[0-9]+" | awk -F: '{print $2}' | tr -d ' '
        exit 1
    fi

    log_success "Environment configured (interface: $HOST_INTERFACE)"
}

# -----------------------------------------------------------------------------
# macvlan Network Setup
# -----------------------------------------------------------------------------

setup_macvlan_host_access() {
    local env_file="$SCRIPT_DIR/.env"
    set -a
    source "$env_file"
    set +a

    log_info "Setting up host access to macvlan containers..."

    # Create a macvlan interface on the host for communication with containers
    local macvlan_bridge="macvlan-shim"
    
    # Remove existing shim if present
    ip link delete "$macvlan_bridge" 2>/dev/null || true

    # Create macvlan shim interface
    ip link add "$macvlan_bridge" link "$HOST_INTERFACE" type macvlan mode bridge
    
    # Assign an IP from the macvlan range to the shim (use .254 as host endpoint)
    local host_shim_ip=$(echo "$NETWORK_SUBNET" | sed 's/\.[0-9]*\/.*/.254/')
    ip addr add "${host_shim_ip}/32" dev "$macvlan_bridge"
    ip link set "$macvlan_bridge" up

    # Add routes to container IPs via the shim
    ip route add "$OLLAMA_IP/32" dev "$macvlan_bridge" 2>/dev/null || true
    ip route add "$WHISPER_IP/32" dev "$macvlan_bridge" 2>/dev/null || true

    log_success "Host macvlan shim configured at $host_shim_ip"

    # Make persistent across reboots
    cat > /etc/systemd/system/macvlan-shim.service <<EOF
[Unit]
Description=macvlan shim for container host access
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c 'ip link delete $macvlan_bridge 2>/dev/null || true; ip link add $macvlan_bridge link $HOST_INTERFACE type macvlan mode bridge; ip addr add ${host_shim_ip}/32 dev $macvlan_bridge; ip link set $macvlan_bridge up; ip route add $OLLAMA_IP/32 dev $macvlan_bridge 2>/dev/null || true; ip route add $WHISPER_IP/32 dev $macvlan_bridge 2>/dev/null || true'
ExecStop=/bin/bash -c 'ip link delete $macvlan_bridge 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable macvlan-shim.service

    log_success "macvlan shim service enabled for persistence"
}

# -----------------------------------------------------------------------------
# Deploy Services
# -----------------------------------------------------------------------------

deploy_services() {
    log_info "Deploying services with Docker Compose..."

    cd "$SCRIPT_DIR"

    # Pull images first
    docker compose pull

    # Start services
    docker compose up -d

    log_success "Services deployed"
}

# -----------------------------------------------------------------------------
# Pull Ollama Model
# -----------------------------------------------------------------------------

pull_ollama_model() {
    local env_file="$SCRIPT_DIR/.env"
    set -a
    source "$env_file"
    set +a

    log_info "Waiting for Ollama to be ready..."
    
    local max_attempts=30
    local attempt=0
    
    while [[ $attempt -lt $max_attempts ]]; do
        if docker exec ollama ollama list &> /dev/null; then
            break
        fi
        attempt=$((attempt + 1))
        log_info "Waiting for Ollama... ($attempt/$max_attempts)"
        sleep 10
    done

    if [[ $attempt -eq $max_attempts ]]; then
        log_error "Ollama did not become ready in time"
        exit 1
    fi

    log_info "Pulling model: $OLLAMA_MODEL"
    docker exec ollama ollama pull "$OLLAMA_MODEL"

    log_success "Model $OLLAMA_MODEL pulled successfully"
}

# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------

validate_deployment() {
    local env_file="$SCRIPT_DIR/.env"
    set -a
    source "$env_file"
    set +a

    log_info "Validating deployment..."

    echo ""
    echo "=============================================="
    echo "  Container Status"
    echo "=============================================="
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

    echo ""
    echo "=============================================="
    echo "  Service Endpoints"
    echo "=============================================="
    echo "Ollama API:  http://$OLLAMA_IP:11434"
    echo "Whisper API: http://$WHISPER_IP:9000"

    echo ""
    echo "=============================================="
    echo "  Connectivity Tests"
    echo "=============================================="

    # Test Ollama
    if curl -s --connect-timeout 5 "http://$OLLAMA_IP:11434/api/tags" &> /dev/null; then
        log_success "Ollama API is reachable"
    else
        log_warn "Ollama API not yet reachable (may still be starting)"
    fi

    # Test Whisper
    if curl -s --connect-timeout 5 "http://$WHISPER_IP:9000/" &> /dev/null; then
        log_success "Whisper API is reachable"
    else
        log_warn "Whisper API not yet reachable (may still be starting)"
    fi

    echo ""
    echo "=============================================="
    echo "  Quick Test Commands"
    echo "=============================================="
    echo ""
    echo "# Test Ollama chat:"
    echo "curl http://$OLLAMA_IP:11434/api/chat -d '{"
    echo '  "model": "'"$OLLAMA_MODEL"'",'
    echo '  "messages": [{"role": "user", "content": "Hello!"}],'
    echo '  "stream": false'
    echo "}'"
    echo ""
    echo "# Test Whisper transcription:"
    echo "curl -X POST http://$WHISPER_IP:9000/asr -F 'audio_file=@audio.wav'"
    echo ""

    log_success "Deployment validation complete"
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

main() {
    echo ""
    echo "=============================================="
    echo "  Jetson Edge AI Point - Installation"
    echo "=============================================="
    echo ""

    check_root
    check_architecture
    check_jetson

    log_info "Starting installation..."

    install_docker
    install_docker_compose
    install_nvidia_runtime
    setup_environment
    deploy_services
    setup_macvlan_host_access
    pull_ollama_model
    validate_deployment

    echo ""
    log_success "Installation complete!"
    echo ""
    echo "Next steps:"
    echo "  1. Edit .env with your GOOGLE_API_KEY and GOOGLE_CX_ID"
    echo "  2. Configure your n8n AI Agent to use the service endpoints"
    echo "  3. Check logs: docker compose logs -f"
    echo ""
}

main "$@"
