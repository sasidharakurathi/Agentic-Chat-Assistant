"""A short catalog of well-known MCP servers (task 4.8).

Starting points, not endorsements: each is a server whose launch command
or endpoint is published by its maintainer, with a link to their docs.
Choosing one fills the MCP tab's Add form; the builder still reviews it,
supplies any secrets (never stored here), and then discovers and chooses its
tools like any other server.

Every preset must pass the same registration rules as a hand-entered
server; `tests/test_mcp_presets.py` checks that, so the catalog cannot drift
into something the API would refuse.

Local-command presets start with `npx` or `uvx`, which download the package
on first use. On a developer machine that just works. In Docker the runner
has no route to the internet by default (EXPLAINER §10.6): bake the package
into the runner image, or use `docker-compose.mcp-egress.yml`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class SecretField:
    """A header or environment variable the builder must supply."""

    name: str
    hint: str


@dataclass(frozen=True)
class McpPreset:
    key: str
    #: The server name the form suggests (the tool-name prefix).
    name: str
    title: str
    description: str
    transport: Literal["stdio", "http", "sse"]
    docs_url: str
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    env: tuple[SecretField, ...] = field(default_factory=tuple)
    headers: tuple[SecretField, ...] = field(default_factory=tuple)
    #: Needs the internet at run time (to download itself, or to do its job).
    needs_network: bool = True


PRESETS: tuple[McpPreset, ...] = (
    McpPreset(
        key="everything",
        name="everything",
        title="Everything (MCP test server)",
        description=(
            "The MCP project's reference server that exercises every feature of the "
            "protocol. Useful for trying the platform out; it does nothing real."
        ),
        transport="stdio",
        command="npx",
        args=("-y", "@modelcontextprotocol/server-everything"),
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/everything",
    ),
    McpPreset(
        key="time",
        name="time",
        title="Time",
        description="Current time and time-zone conversion.",
        transport="stdio",
        command="uvx",
        args=("mcp-server-time",),
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/time",
    ),
    McpPreset(
        key="fetch",
        name="fetch",
        title="Fetch",
        description=(
            "Fetches web pages and returns them as Markdown. Runs in the MCP runner, so "
            "it reaches only what the runner's network allows; the built-in HTTP tool is "
            "the SSRF-guarded alternative."
        ),
        transport="stdio",
        command="uvx",
        args=("mcp-server-fetch",),
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
    ),
    McpPreset(
        key="memory",
        name="memory",
        title="Memory (knowledge graph)",
        description=(
            "A small knowledge graph the model can write to and read from. It lives in "
            "the server's private directory, so it lasts only as long as one session."
        ),
        transport="stdio",
        command="npx",
        args=("-y", "@modelcontextprotocol/server-memory"),
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
    ),
    McpPreset(
        key="sequential-thinking",
        name="thinking",
        title="Sequential thinking",
        description="A structured scratchpad for breaking a problem into revisable steps.",
        transport="stdio",
        command="npx",
        args=("-y", "@modelcontextprotocol/server-sequential-thinking"),
        docs_url=(
            "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking"
        ),
    ),
    McpPreset(
        key="github",
        name="github",
        title="GitHub (remote)",
        description=(
            "GitHub's hosted MCP server: issues, pull requests, code search. Needs a "
            "personal access token; give it only the scopes this assistant needs. Check "
            "GitHub's docs for the current endpoint before relying on it."
        ),
        transport="http",
        url="https://api.githubcopilot.com/mcp/",
        headers=(
            SecretField(
                name="Authorization",
                hint="Bearer <a GitHub personal access token>",
            ),
        ),
        docs_url="https://github.com/github/github-mcp-server",
    ),
)


def by_key(key: str) -> McpPreset | None:
    return next((p for p in PRESETS if p.key == key), None)


__all__ = ["PRESETS", "McpPreset", "SecretField", "by_key"]
