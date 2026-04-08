#!/bin/bash













set -e


C2_PORT=1324
PYTHON_MIN_VERSION=3.8
DATA_DIR="./data"


RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'


print_banner() {
    echo -e "${GREEN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║  Noxveil — Launcher                                       ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_info() {
    echo -e "${GREEN}[*]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_error() {
    echo -e "${RED}[X]${NC} $1"
}


NO_TUNNEL=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-tunnel)
            NO_TUNNEL=true
            shift
            ;;
        --port)
            C2_PORT="$2"
            shift 2
            ;;
        *)
            echo "Usage: $0 [--no-tunnel] [--port PORT]"
            exit 1
            ;;
    esac
done


if [ "$C2_PORT" -lt 1024 ] && [ "$(id -u)" -ne 0 ]; then
    print_warning "Port $C2_PORT requires root. Run with sudo?"
fi



check_cloudflared() {
    print_info "Checking cloudflared..."

    if command -v cloudflared &> /dev/null; then
        CLOUDFLARED_VERSION=$(cloudflared --version 2>&1 | head -n1)
        print_info "cloudflared found: $CLOUDFLARED_VERSION ✓"
        return 0
    fi

    print_warning "cloudflared not found. Attempting to install..."


    OS=$(uname -s)
    ARCH=$(uname -m)

    case "$OS" in
        Linux)
            case "$ARCH" in
                x86_64)
                    CLOUDFLARED_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
                    ;;
                aarch64|arm64)
                    CLOUDFLARED_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
                    ;;
                *)
                    print_error "Unsupported architecture: $ARCH"
                    exit 1
                    ;;
            esac
            ;;
        Darwin)
            case "$ARCH" in
                x86_64)
                    CLOUDFLARED_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64"
                    ;;
                arm64)
                    CLOUDFLARED_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64"
                    ;;
                *)
                    print_error "Unsupported architecture: $ARCH"
                    exit 1
                    ;;
            esac
            ;;
        MINGW*|MSYS*|CYGWIN*)
            CLOUDFLARED_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
            ;;
        *)
            print_error "Unsupported OS: $OS"
            exit 1
            ;;
    esac


    print_info "Downloading cloudflared from GitHub..."

    TEMP_DIR=$(mktemp -d)
    CLOUDFLARED_BIN="$TEMP_DIR/cloudflared"

    if curl -L --connect-timeout 30 --max-time 120 -o "$CLOUDFLARED_BIN" "$CLOUDFLARED_URL"; then
        chmod +x "$CLOUDFLARED_BIN"


        if [ -w /usr/local/bin ]; then
            sudo mv "$CLOUDFLARED_BIN" /usr/local/bin/cloudflared
            print_info "cloudflared installed to /usr/local/bin ✓"
        else
            mkdir -p "$HOME/bin"
            mv "$CLOUDFLARED_BIN" "$HOME/bin/cloudflared"
            export PATH="$HOME/bin:$PATH"
            print_info "cloudflared installed to $HOME/bin ✓"
        fi
    else
        print_error "Failed to download cloudflared"
        rm -rf "$TEMP_DIR"
        exit 1
    fi
}


install_dependencies() {
    print_info "Installing Python dependencies..."

    cd server

    if [ -f "requirements.txt" ]; then
        pip3 install -r requirements.txt --quiet
        print_info "Dependencies installed ✓"
    else
        print_error "requirements.txt not found"
        exit 1
    fi

    cd ..
}


setup_directories() {
    print_info "Setting up directories..."

    mkdir -p "$DATA_DIR"
    print_info "Data directory: $DATA_DIR ✓"
}


start_server() {
    print_info "Starting Noxveil on port $C2_PORT..."

    if [ "$NO_TUNNEL" = true ]; then
        print_warning "Running without tunnel (local mode only)"
        export ENABLE_TUNNEL=false
    fi

    PYTHONPATH=. python3 -m server.main
}


main() {
    print_banner

    echo "Checking environment..."

    check_cloudflared
    install_dependencies
    setup_directories

    print_info "Starting server..."
    echo ""

    start_server
}


trap 'print_info "Shutting down..."; exit 0' SIGINT SIGTERM


main
