#!/bin/bash
# =============================================================================
# Jetson Edge AI Point - Uninstall & Cleanup Script
# =============================================================================
# Removes all containers, networks, volumes, images, and optionally Docker
# Leaves the system in a pre-install state
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

confirm_action() {
    local prompt="$1"
    local default="${2:-n}"
    
    if [[ "$FORCE_YES" == "true" ]]; then
        return 0
    fi

    read -p "$prompt [y/N]: " response
    case "$response" in
        [yY][eE][sS]|[yY]) return 0 ;;
        *) return 1 ;;
    esac
}

# -----------------------------------------------------------------------------
# Stop and Remove Containers
# -----------------------------------------------------------------------------

stop_containers() {
    log_info "Stopping containers..."

    cd "$SCRIPT_DIR"

    if [[ -f "docker-compose.yml" ]]; then
        docker compose down --remove-orphans 2>/dev/null || true
    fi

    # Force stop any remaining project containers
    for container in ollama whisper watchtower; do
        if docker ps -a --format '{{.Names}}' | grep -q "^${container}$"; then
            log_info "Stopping container: $container"
            docker stop "$container" 2>/dev/null || true
            docker rm -f "$container" 2>/dev/null || true
        fi
    done

    log_success "Containers stopped and removed"
}

# -----------------------------------------------------------------------------
# Remove Docker Networks
# -----------------------------------------------------------------------------

remove_networks() {
    log_info "Removing Docker networks..."

    # Remove project-specific macvlan network
    local network_name="${PWD##*/}_edge_macvlan"
    docker network rm "$network_name" 2>/dev/null || true
    docker network rm "edge_macvlan" 2>/dev/null || true
    
    # Also try with common compose prefixes
    docker network ls --format '{{.Name}}' | grep -E "edge_macvlan|jetson.*macvlan" | while read net; do
        log_info "Removing network: $net"
        docker network rm "$net" 2>/dev/null || true
    done

    log_success "Docker networks removed"
}

# -----------------------------------------------------------------------------
# Remove Volumes
# -----------------------------------------------------------------------------

remove_volumes() {
    log_info "Removing Docker volumes..."

    # Remove named volumes
    docker volume rm ollama_data 2>/dev/null || true

    # Remove any project-prefixed volumes
    docker volume ls --format '{{.Name}}' | grep -E "ollama|whisper" | while read vol; do
        log_info "Removing volume: $vol"
        docker volume rm "$vol" 2>/dev/null || true
    done

    log_success "Docker volumes removed"
}

# -----------------------------------------------------------------------------
# Remove Images
# -----------------------------------------------------------------------------

remove_images() {
    log_info "Removing Docker images..."

    local images=(
        "ollama/ollama"
        "onerahmet/openai-whisper-asr-webservice"
        "containrrr/watchtower"
    )

    for image in "${images[@]}"; do
        if docker images --format '{{.Repository}}' | grep -q "^${image}$"; then
            log_info "Removing image: $image"
            docker rmi -f "$image" 2>/dev/null || true
        fi
    done

    # Remove dangling images
    docker image prune -f 2>/dev/null || true

    log_success "Docker images removed"
}

# -----------------------------------------------------------------------------
# Remove macvlan Host Shim
# -----------------------------------------------------------------------------

remove_macvlan_shim() {
    log_info "Removing macvlan host shim..."

    # Stop and disable the systemd service
    systemctl stop macvlan-shim.service 2>/dev/null || true
    systemctl disable macvlan-shim.service 2>/dev/null || true
    rm -f /etc/systemd/system/macvlan-shim.service
    systemctl daemon-reload

    # Remove the interface
    ip link delete macvlan-shim 2>/dev/null || true

    log_success "macvlan shim removed"
}

# -----------------------------------------------------------------------------
# Remove Environment Files
# -----------------------------------------------------------------------------

remove_env_files() {
    log_info "Removing environment files..."

    if [[ -f "$SCRIPT_DIR/.env" ]]; then
        rm -f "$SCRIPT_DIR/.env"
        log_info "Removed .env"
    fi

    log_success "Environment files removed"
}

# -----------------------------------------------------------------------------
# Remove Docker Completely (Optional)
# -----------------------------------------------------------------------------

remove_docker() {
    if ! confirm_action "Do you want to completely remove Docker?"; then
        log_info "Skipping Docker removal"
        return
    fi

    log_warn "Removing Docker completely..."

    # Stop Docker service
    systemctl stop docker.service 2>/dev/null || true
    systemctl stop docker.socket 2>/dev/null || true

    # Remove Docker packages
    apt-get purge -y docker-ce docker-ce-cli containerd.io docker-compose-plugin 2>/dev/null || true
    apt-get autoremove -y 2>/dev/null || true

    # Remove Docker data directories
    rm -rf /var/lib/docker
    rm -rf /var/lib/containerd
    rm -rf /etc/docker

    # Remove Docker GPG key and repository
    rm -f /usr/share/keyrings/docker-archive-keyring.gpg
    rm -f /etc/apt/sources.list.d/docker.list

    # Remove NVIDIA container toolkit
    apt-get purge -y nvidia-container-toolkit 2>/dev/null || true
    rm -f /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    rm -f /etc/apt/sources.list.d/nvidia-container-toolkit.list

    log_success "Docker completely removed"
}

# -----------------------------------------------------------------------------
# Cleanup Summary
# -----------------------------------------------------------------------------

show_summary() {
    echo ""
    echo "=============================================="
    echo "  Cleanup Summary"
    echo "=============================================="
    echo ""
    echo "Removed:"
    echo "  ✓ Containers (ollama, whisper, watchtower)"
    echo "  ✓ Docker networks (macvlan)"
    echo "  ✓ Docker volumes (ollama_data)"
    echo "  ✓ Docker images"
    echo "  ✓ macvlan host shim interface"
    echo "  ✓ Environment files (.env)"
    echo ""
    
    if [[ "$REMOVE_DOCKER" == "true" ]]; then
        echo "  ✓ Docker and NVIDIA container runtime"
    else
        echo "  - Docker installation preserved"
    fi
    
    echo ""
    log_success "System restored to pre-install state"
    echo ""
}

# -----------------------------------------------------------------------------
# Usage
# -----------------------------------------------------------------------------

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -y, --yes           Skip confirmation prompts"
    echo "  -d, --remove-docker Also remove Docker completely"
    echo "  -h, --help          Show this help message"
    echo ""
    echo "Examples:"
    echo "  sudo $0              # Interactive cleanup"
    echo "  sudo $0 -y           # Non-interactive cleanup"
    echo "  sudo $0 -y -d        # Full cleanup including Docker"
    echo ""
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

main() {
    FORCE_YES="false"
    REMOVE_DOCKER="false"

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            -y|--yes)
                FORCE_YES="true"
                shift
                ;;
            -d|--remove-docker)
                REMOVE_DOCKER="true"
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done

    echo ""
    echo "=============================================="
    echo "  Jetson Edge AI Point - Uninstall"
    echo "=============================================="
    echo ""

    check_root

    if [[ "$FORCE_YES" != "true" ]]; then
        echo "This will remove:"
        echo "  - All project containers (ollama, whisper, watchtower)"
        echo "  - Docker networks and volumes"
        echo "  - Downloaded Docker images"
        echo "  - macvlan network configuration"
        echo ""
        
        if ! confirm_action "Are you sure you want to proceed?"; then
            log_info "Uninstall cancelled"
            exit 0
        fi
    fi

    log_info "Starting cleanup..."

    stop_containers
    remove_networks
    remove_volumes
    remove_images
    remove_macvlan_shim
    remove_env_files

    if [[ "$REMOVE_DOCKER" == "true" ]]; then
        remove_docker
    fi

    show_summary
}

main "$@"
