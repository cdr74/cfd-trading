#!/usr/bin/env bash
# Pull latest images from ghcr.io and start both MCP servers as Podman containers.
# Run from any location — paths are resolved relative to this script's position.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  !${NC} $*"; }
fail() { echo -e "${RED}  ✗${NC} $*"; }

echo ""
echo "Starting MCP servers..."
echo ""

# ── capital-mcp-server ────────────────────────────────────────────────────────
cd "$WORKSPACE_DIR/capital-mcp-server"
if podman ps --format "{{.Names}}" | grep -q "^capital-mcp-server$"; then
    warn "capital-mcp-server already running — stopping before update"
    podman stop capital-mcp-server 2>&1 | tail -1
    podman rm   capital-mcp-server 2>&1 | tail -1
fi
echo "  Pulling capital-mcp-server..."
podman pull ghcr.io/cdr74/capital-mcp-server:latest 2>&1 | tail -1
podman-compose up -d 2>&1 | tail -1
ok "capital-mcp-server started"

# ── cfd-trading ───────────────────────────────────────────────────────────────
cd "$WORKSPACE_DIR/cfd-trading"
CFD_RUNNING=$(podman ps --format "{{.Names}}" | grep -E "^cfd-trading" || true)
if [[ -n "$CFD_RUNNING" ]]; then
    warn "$CFD_RUNNING already running — stopping before update"
    podman stop "$CFD_RUNNING" 2>&1 | tail -1
    podman rm   "$CFD_RUNNING" 2>&1 | tail -1
fi
echo "  Pulling cfd-trading..."
podman pull ghcr.io/cdr74/cfd-trading:latest 2>&1 | tail -1
podman-compose up -d 2>&1 | tail -1
ok "cfd-trading started"

echo ""
echo "Waiting for containers to become ready..."
sleep 3

# ── quick health check ────────────────────────────────────────────────────────
echo ""
PASS=true

for name in capital-mcp-server cfd-trading; do
    status=$(podman inspect "$name" --format "{{.State.Status}}" 2>/dev/null || echo "missing")
    if [[ "$status" == "running" ]]; then
        ok "$name is running"
    else
        fail "$name status: $status"
        PASS=false
    fi
done

echo ""
if $PASS; then
    echo -e "${GREEN}Both servers running. Run mcp-status.sh for endpoint and config checks.${NC}"
else
    echo -e "${RED}One or more servers failed to start. Check: podman logs <name>${NC}"
    exit 1
fi
echo ""
