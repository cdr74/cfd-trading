#!/usr/bin/env bash
# Restore MCP server entries in Claude Desktop config.
#
# Claude Desktop occasionally overwrites claude_desktop_config.json on restart,
# removing the mcpServers block. Run this script to restore it, then restart
# Claude Desktop.

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

CONFIG="/mnt/c/Users/chris/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/claude_desktop_config.json"

if [[ ! -f "$CONFIG" ]]; then
    echo -e "${RED}  ✗${NC} Config file not found: $CONFIG"
    echo "  Is Claude Desktop installed?"
    exit 1
fi

echo ""
echo "Current config:"
cat "$CONFIG"
echo ""

# Merge mcpServers into existing config (preserves preferences and any other keys)
python3 - <<'PYEOF'
import json, sys

config_path = "/mnt/c/Users/chris/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/claude_desktop_config.json"

with open(config_path) as f:
    cfg = json.load(f)

cfg["mcpServers"] = {
    "cfd-trading": {
        "command": "npx",
        "args": ["-y", "mcp-remote", "https://localhost:8089/mcp"],
        "env": {"NODE_OPTIONS": "--use-system-ca"}
    },
    "capital-mcp-server": {
        "command": "npx",
        "args": ["-y", "mcp-remote", "https://localhost:8088/mcp"],
        "env": {"NODE_OPTIONS": "--use-system-ca"}
    }
}

with open(config_path, "w") as f:
    json.dump(cfg, f, indent=2)

print("Done.")
PYEOF

echo -e "${GREEN}  ✓${NC} mcpServers block restored"
echo ""
echo "Updated config:"
cat "$CONFIG"
echo ""
echo -e "${YELLOW}  !${NC} Restart Claude Desktop for changes to take effect."
echo ""
