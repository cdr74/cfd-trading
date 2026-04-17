"""CFD Trading MCP Server — exposes session, scan, and trade tools to Claude Code."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(name="cfd-trading")


def main():
    mcp.run()


if __name__ == "__main__":
    main()
