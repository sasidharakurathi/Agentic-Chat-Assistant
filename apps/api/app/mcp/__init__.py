"""MCP servers (Phase 4): running them safely and talking to them.

- `limits`: the resource limits a stdio server runs under.
- `jail`: applies those limits to a process, then becomes the server.
- `runner`: the sidecar that starts stdio servers and serves them over MCP's
  HTTP transport, so that untrusted code never runs inside the API.

The API's side of the runner is `app/services/mcp_runner.py`. This package is
kept free of `app.config` and the database: the runner imports this package
and must not load the platform's settings, which hold its secrets.
"""
