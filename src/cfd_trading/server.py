"""CFD Trading MCP Server — exposes session, scan, and trade tools to Claude Code."""

import logging
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

from cfd_trading.tools.session_tools import start_session, end_session, get_session_status
from cfd_trading.tools.scan_tools import scan_markets, analyze_instrument
from cfd_trading.tools.trade_tools import validate_proposal, execute_trade

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

mcp = FastMCP(
    name="cfd-trading",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8000")),
)

mcp.tool()(start_session)
mcp.tool()(end_session)
mcp.tool()(get_session_status)
mcp.tool()(scan_markets)
mcp.tool()(analyze_instrument)
mcp.tool()(validate_proposal)
mcp.tool()(execute_trade)


def main():
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    ssl_certfile = os.getenv("SSL_CERTFILE")
    ssl_keyfile = os.getenv("SSL_KEYFILE")

    if transport == "streamable-http" and ssl_certfile and ssl_keyfile:
        import uvicorn
        logging.getLogger(__name__).info(f"Using streamable HTTPS transport on port {mcp.settings.port}")
        config = uvicorn.Config(
            mcp.streamable_http_app(),
            host=mcp.settings.host,
            port=mcp.settings.port,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
            log_level=mcp.settings.log_level.lower(),
        )
        uvicorn.Server(config).run()
    else:
        mcp.run(transport=transport)


if __name__ == "__main__":
    main()
