#!/usr/bin/env bash
# Stop both MCP servers.
# Run from any location — paths are resolved relative to this script's position.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  !${NC} $*"; }

echo ""
echo "Stopping MCP servers..."
echo ""

CFD_RUNNING=$(podman ps --format "{{.Names}}" | grep -E "^cfd-trading" || true)
if [[ -n "$CFD_RUNNING" ]]; then
    podman stop "$CFD_RUNNING" 2>&1 | tail -1
    podman rm   "$CFD_RUNNING" 2>&1 | tail -1
    ok "$CFD_RUNNING stopped"
else
    warn "cfd-trading was not running"
fi

cd "$WORKSPACE_DIR/capital-mcp-server"
if podman ps --format "{{.Names}}" | grep -q "^capital-mcp-server$"; then
    podman-compose down 2>&1 | tail -1
    ok "capital-mcp-server stopped"
else
    warn "capital-mcp-server was not running"
fi

echo ""
