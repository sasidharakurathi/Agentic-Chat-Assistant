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

API and worker Docker images get the Claude Code CLI from `claude-agent-sdk` itself, which bundles it as a native binary; its version is pinned by the SDK's. (See the amendment below.)

## Consequences

- Positive: tenants can never reach the filesystem/shell; all capability code is ours.
- Negative: subprocess-per-session has throughput/memory cost — mitigated by a bounded
  concurrency semaphore and reusing `ClaudeSDKClient` per conversation; documented fallback
  is the direct-API tool runner.
- Revisit: if subprocess overhead dominates under load (Phase 6 load test).

## Amendment — 2026-09-24

Two statements above no longer matched the code, found by the Phase 0–3 audit:

- **Packaging.** The images installed Node and npm for a CLI they never used:
  `claude-agent-sdk` ships the CLI inside its wheel as a self-contained native
  binary, verified by running it with Node removed from the PATH. The Node layer
  is gone (420 MB smaller), and the CLI version is pinned by the SDK version.
- **Mitigations.** The bounded concurrency semaphore now exists
  (`AGENT_MAX_CONCURRENCY`, waiting up to `AGENT_QUEUE_WAIT_S` before a clear
  `busy` error). **Reusing one `ClaudeSDKClient` per conversation is deliberately
  deferred**, for these reasons:
  - A turn's options (model, effort, tools, budget remaining) are rebuilt from
    the assistant's config every turn and can change between turns. A reused
    client would keep running the old ones.
  - Context is already carried across turns by resuming the SDK session
    (`options.resume`), so reuse would buy latency, not correctness.
  - A long-lived client holds a subprocess per idle conversation, which is the
    memory cost this mitigation was meant to reduce. It is also pinned to one
    worker.

  Whether per-turn startup is worth optimising is exactly what the Phase 6 load
  test (6.5) measures. Decide then, with numbers.

