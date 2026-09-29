# syntax=docker/dockerfile:1
# The MCP runner (task 4.4). Build context = repo root.
#
# Holds none of the platform's secrets and none of its code beyond the
# runner itself: stdio MCP servers run here, so this image is what a
# builder's `npx some-package` can see. Node.js and uv are included because
# most published MCP servers are started with `npx` or `uvx`.

FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Pinned to what the API resolves (claude-agent-sdk brings mcp 2.x).
RUN pip install "mcp==2.1.1" "uv"

RUN groupadd --system --gid 10001 runner \
    && useradd --system --uid 10001 --gid runner --home /tmp --shell /usr/sbin/nologin runner

# Only the runner, its jail, and the pure helpers they import.
WORKDIR /srv
COPY apps/api/app/__init__.py app/__init__.py
COPY apps/api/app/mcp app/mcp
COPY apps/api/app/security/__init__.py app/security/__init__.py
COPY apps/api/app/security/redact.py app/security/redact.py

USER runner
EXPOSE 8100
ENV MCP_RUNNER_HOST=0.0.0.0 \
    MCP_RUNNER_PORT=8100

HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8100/healthz').status==200 else 1)"

CMD ["python", "-m", "app.mcp.runner"]
