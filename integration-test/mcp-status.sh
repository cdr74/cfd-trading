#!/usr/bin/env bash
# Full status and config check for both MCP servers.
# Verifies: containers, HTTPS endpoints, .env credentials, Claude Desktop config.
# Run from any location — paths are resolved relative to this script's position.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'

ok()      { echo -e "${GREEN}  ✓${NC} $*"; }
warn()    { echo -e "${YELLOW}  !${NC} $*"; }
fail()    { echo -e "${RED}  ✗${NC} $*"; FAILURES=$((FAILURES+1)); }
section() { echo ""; echo -e "${BOLD}$*${NC}"; }

FAILURES=0
CLAUDE_DESKTOP_CONFIG="/mnt/c/Users/chris/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/claude_desktop_config.json"

# ── 1. Container status ───────────────────────────────────────────────────────
section "1. Container status"

check_container() {
    local name=$1 port=$2 endpoint=$3
    local status
    status=$(podman inspect "$name" --format "{{.State.Status}}" 2>/dev/null || echo "missing")

    if [[ "$status" == "running" ]]; then
        local health
        health=$(podman inspect "$name" --format "{{.State.Health.Status}}" 2>/dev/null || echo "")
        if [[ -n "$health" ]]; then
            ok "$name — running (health: $health) — port $port"
        else
            ok "$name — running — port $port"
        fi
    elif [[ "$status" == "missing" ]]; then
        fail "$name — not found (not started)"
        return
    else
        fail "$name — $status"
        return
    fi

    local response
    response=$(curl -sk --max-time 3 -o /dev/null -w "%{http_code}" \
        -X POST -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -d '{"jsonrpc":"2.0","method":"initialize","id":1,"params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"healthcheck","version":"1"}}}' \
        "$endpoint" 2>/dev/null; echo "")
    response="${response//[^0-9]/}"
    response="${response:0:3}"
    if [[ "$response" == "200" ]]; then
        ok "$name — endpoint $endpoint responding"
    else
        fail "$name — endpoint $endpoint not responding (HTTP $response)"
    fi
}

check_container "capital-mcp-server" "8088" "https://localhost:8088/mcp"
check_container "cfd-trading"        "8089" "https://localhost:8089/mcp"

# ── 2. .env credential checks ─────────────────────────────────────────────────
section "2. Credentials (.env files)"

check_env_var() {
    local file=$1 var=$2
    if ! grep -q "^${var}=" "$file" 2>/dev/null; then
        fail "$var — missing from $file"
        return
    fi
    local value
    value=$(grep "^${var}=" "$file" | cut -d= -f2-)
    if [[ -z "$value" ]]; then
        fail "$var — empty in $file"
    else
        ok "$var — set"
    fi
}

CAPITAL_ENV="$WORKSPACE_DIR/capital-mcp-server/.env"
CFD_ENV="$WORKSPACE_DIR/cfd-trading/.env"

if [[ -f "$CAPITAL_ENV" ]]; then
    echo "  capital-mcp-server/.env"
    for var in CAPITAL_BASE_URL CAPITAL_API_KEY CAPITAL_IDENTIFIER CAPITAL_API_KEY_PASSWORD; do
        check_env_var "$CAPITAL_ENV" "$var"
    done
else
    fail "capital-mcp-server/.env — file not found (copy from .env.example)"
fi

echo ""
if [[ -f "$CFD_ENV" ]]; then
    echo "  cfd-trading/.env"
    for var in CAPITAL_BASE_URL CAPITAL_API_KEY CAPITAL_IDENTIFIER CAPITAL_API_KEY_PASSWORD; do
        check_env_var "$CFD_ENV" "$var"
    done
else
    fail "cfd-trading/.env — file not found (copy from .env.example)"
fi

# ── 3. Claude Desktop config ──────────────────────────────────────────────────
section "3. Claude Desktop config"

if [[ ! -f "$CLAUDE_DESKTOP_CONFIG" ]]; then
    fail "claude_desktop_config.json not found at expected path"
else
    ok "claude_desktop_config.json found"

    # Check cfd-trading entry
    if python3 -c "
import json, sys
cfg = json.load(open('$CLAUDE_DESKTOP_CONFIG'))
servers = cfg.get('mcpServers', {})
if 'cfd-trading' not in servers:
    sys.exit(1)
entry = servers['cfd-trading']
args = entry.get('args', [])
if not any('https://localhost:8089/mcp' in a for a in args):
    sys.exit(2)
env = entry.get('env', {})
if '--use-system-ca' not in env.get('NODE_OPTIONS', ''):
    sys.exit(3)
" 2>/dev/null; then
        ok "cfd-trading entry — mcp-remote → https://localhost:8089/mcp (NODE_OPTIONS: --use-system-ca)"
    else
        fail "cfd-trading entry missing or wrong config in claude_desktop_config.json — run mcp-fix-config.sh"
    fi

    # Check capital-mcp-server entry
    if python3 -c "
import json, sys
cfg = json.load(open('$CLAUDE_DESKTOP_CONFIG'))
servers = cfg.get('mcpServers', {})
if 'capital-mcp-server' not in servers:
    sys.exit(1)
entry = servers['capital-mcp-server']
args = entry.get('args', [])
if not any('https://localhost:8088/mcp' in a for a in args):
    sys.exit(2)
env = entry.get('env', {})
if '--use-system-ca' not in env.get('NODE_OPTIONS', ''):
    sys.exit(3)
" 2>/dev/null; then
        ok "capital-mcp-server entry — mcp-remote → https://localhost:8088/mcp (NODE_OPTIONS: --use-system-ca)"
    else
        fail "capital-mcp-server entry missing or wrong config in claude_desktop_config.json — run mcp-fix-config.sh"
    fi
fi

# ── 4. Runtime directories ────────────────────────────────────────────────────
section "4. Runtime directories"

if [[ -d "$WORKSPACE_DIR/cfd-trading/data" ]]; then
    ok "cfd-trading/data/ exists"
else
    warn "cfd-trading/data/ missing — will be created on first session start"
fi

if [[ -d "$WORKSPACE_DIR/capital-mcp-server/logs" ]]; then
    ok "capital-mcp-server/logs/ exists"
else
    warn "capital-mcp-server/logs/ missing — will be created on container start"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
if [[ $FAILURES -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}All checks passed. System ready for smoke testing.${NC}"
else
    echo -e "${RED}${BOLD}$FAILURES check(s) failed. Fix the issues above before smoke testing.${NC}"
    exit 1
fi
echo ""
