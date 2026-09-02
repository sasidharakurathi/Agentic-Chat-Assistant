# ADR 0003: Running the Anthropic Agent SDK safely in a multi-tenant server

- **Status:** accepted (design; implemented from Phase 1)
- **Date:** 2026-09-02
- **Deciders:** core team

## Context

The Anthropic Agent SDK (`claude-agent-sdk`) runs the agent loop, MCP, subagents, hooks,
permissions, and sessions in our process, but it spawns the Claude Code CLI as a
subprocess and ships a coding-agent tool set (Bash, Write, Edit, Read, …) that is unsafe
to expose to tenants.

## Decision

Per conversation we build `ClaudeAgentOptions` with:

- `setting_sources=[]` — never load host/user/project `.claude` settings.
- `system_prompt` = our composed string (not the `claude_code` preset).
- `allowed_tools` = an explicit allowlist: `mcp__caps__*` (our in-process capability
  server), enabled user MCP tools, and optionally `WebSearch`.
- `disallowed_tools` = Bash/Write/Edit/Read/Glob/Grep/NotebookEdit/WebFetch.
- `can_use_tool` = deny-by-default approval router.
- `cwd` = a per-conversation empty scratch dir.
- `max_turns`, `max_budget_usd`, `effort`, `thinking` from the Assistant Version config.

Platform capabilities (RAG, SQL, HTTP, calculator) are exposed as an **in-process SDK MCP
server** (`create_sdk_mcp_server` + `@tool`) so they run with tenant context and DB pools
and need no extra network hop.

API and worker Docker images bundle Node LTS + a pinned `@anthropic-ai/claude-code`.

## Consequences

- Positive: tenants can never reach the filesystem/shell; all capability code is ours.
- Negative: subprocess-per-session has throughput/memory cost — mitigated by a bounded
  concurrency semaphore and reusing `ClaudeSDKClient` per conversation; documented fallback
  is the direct-API tool runner.
- Revisit: if subprocess overhead dominates under load (Phase 6 load test).
