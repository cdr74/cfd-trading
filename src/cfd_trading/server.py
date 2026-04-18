"""CFD Trading MCP Server — exposes session, scan, and trade tools to Claude Code."""

import logging
import os

from mcp.server.fastmcp import FastMCP

from cfd_trading.tools.session_tools import start_session, end_session, get_session_status
from cfd_trading.tools.scan_tools import scan_markets, analyze_instrument
from cfd_trading.tools.trade_tools import validate_proposal, execute_trade

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

mcp = FastMCP(name="cfd-trading")

mcp.tool()(start_session)
mcp.tool()(end_session)
mcp.tool()(get_session_status)
mcp.tool()(scan_markets)
mcp.tool()(analyze_instrument)
mcp.tool()(validate_proposal)
mcp.tool()(execute_trade)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
