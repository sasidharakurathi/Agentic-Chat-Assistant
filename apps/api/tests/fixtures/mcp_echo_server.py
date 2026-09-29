"""A minimal stdio MCP server for tests (tasks 4.4 / 4.9).

`echo` returns its input. `environment` reports what the server can see, so a
test can prove what the sandbox hides: whether a platform secret leaked into
its environment, where its working directory and HOME point, and the one
variable it was given. `spin` burns CPU, to test the CPU limit.
"""

from __future__ import annotations

import json
import os

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

app = MCPServer("echo")


@app.tool(annotations=ToolAnnotations(read_only_hint=True))
def echo(text: str) -> str:
    """Return the text unchanged. (Declared read-only; the others are not.)"""
    return text


@app.tool()
def environment() -> str:
    """Report what this process can see."""
    leaked = sorted(
        k
        for k in os.environ
        if k in {"APP_KEK", "JWT_SECRET", "DATABASE_URL", "ANTHROPIC_API_KEY", "MCP_RUNNER_TOKEN"}
    )
    return json.dumps(
        {
            "cwd": os.getcwd(),
            "home": os.environ.get("HOME"),
            "leaked": leaked,
            "demo_token": os.environ.get("DEMO_TOKEN"),
        }
    )


@app.tool()
def spin(seconds: float) -> str:
    """Burn CPU for about this long."""
    import time

    end = time.monotonic() + seconds
    n = 0
    while time.monotonic() < end:
        n += 1
    return str(n)


if __name__ == "__main__":
    import sys

    # `mcp_echo_server.py http <port>` or `sse <port>` serves it remotely,
    # for the http/sse discovery tests; no arguments means stdio.
    if len(sys.argv) > 2:
        mode, port = sys.argv[1], int(sys.argv[2])
        if mode == "sse":
            app.run("sse", host="127.0.0.1", port=port)
        else:
            app.run("streamable-http", host="127.0.0.1", port=port)
    else:
        app.run()
