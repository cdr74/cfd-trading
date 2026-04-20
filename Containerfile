FROM python:3.12-slim

WORKDIR /app

# Build context must be the trading/ parent directory (podman-compose.dev.yml sets context: ..)
# Install the full dependency chain from sibling repos — no internet needed during build.
# --no-deps avoids pip re-fetching git+https:// URL deps that are already installed locally.

COPY capital-com-client/pyproject.toml capital-com-client/README.md ./capital-com-client/
COPY capital-com-client/src/ ./capital-com-client/src/
RUN pip install --no-cache-dir ./capital-com-client/

COPY capital-mcp-server/pyproject.toml capital-mcp-server/README.md ./capital-mcp-server/
COPY capital-mcp-server/capital_mcp_server.py ./capital-mcp-server/
RUN pip install --no-cache-dir --no-deps ./capital-mcp-server/ \
    && pip install --no-cache-dir \
        "fastmcp>=3.0.0,<4.0.0" \
        "requests>=2.25.0" \
        "pydantic>=2.0.0" \
        "python-dotenv>=1.0.0"

COPY cfd-trading/pyproject.toml cfd-trading/README.md ./
COPY cfd-trading/src/ ./src/
COPY cfd-trading/config/ ./config/
RUN pip install --no-cache-dir --no-deps . \
    && pip install --no-cache-dir \
        "anthropic>=0.40.0" \
        "mcp[cli]>=1.0.0" \
        "pyyaml>=6.0" \
        "requests>=2.31.0" \
        "pydantic>=2.0.0" \
        "python-dotenv>=1.0.0"

RUN mkdir -p /app/data /app/logs

ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8089
ENV LOG_LEVEL=INFO
ENV SSL_CERTFILE=/certs/cert.pem
ENV SSL_KEYFILE=/certs/key.pem

EXPOSE 8089

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c \
        "import socket,sys; s=socket.socket(); s.settimeout(2); \
         r=s.connect_ex(('127.0.0.1',8089)); s.close(); sys.exit(r)"

CMD ["cfd-trading"]
