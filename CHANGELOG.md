# Changelog

What changed in each release of Assistant Studio, for the people who run
it and the people who build on it. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

How each piece works, and why, is in [`docs/EXPLAINER.md`](docs/EXPLAINER.md)
(the section numbers below point into it). The release notes for 1.0.0,
with what was measured and what is known to be missing, are in
[`docs/RELEASE_NOTES_v1.0.0.md`](docs/RELEASE_NOTES_v1.0.0.md).

## [Unreleased]

### Changed

- **An HTTP request tool with no allowed sites now reaches no sites**
  (it used to reach any public site). Assistants that use it with an empty
  list stop fetching until their builder lists the sites; the builder's
  warnings say so, and sites where anyone can publish are flagged
  (EXPLAINER §16, 7a.9).
- The MCP server catalog (`GET /mcp-presets`) and runner status
  (`GET /mcp-runner`) need an org membership (the `X-Org-Id` header), not
  only a signed-in user. The web app already sends it.
- The proxy keeps a JSON access log for about 183 days on a new volume,
  `caddylogs`, with invite tokens and file-link signatures cut out; the
  API's access line names the user and the client address; the audit log
  is append-only (a Postgres trigger, migration `b7a8c0d1e2f3`).
  OPERATIONS.md has an incident runbook (7a.8).
- New named volume `agentstate` on the API and the worker
  (`/var/lib/assistant-studio`, `AGENT_STATE_DIR`). It is a cache and is
  not backed up (OPERATIONS.md section 5).
- The object store is Chainguard's MinIO build, pinned by digest, since
  `minio/minio` is no longer published. A one-off `minio-owner` step hands
  an existing data volume to the new image's user; nothing to do by hand.
- Every Python dependency is pinned exactly (`apps/api/constraints.txt`);
  the image and CI install under it. `claude-agent-sdk` is 0.2.164
  (Claude CLI 2.1.292), the first line new enough for Opus 5.5.
- Assistants name model families ("sonnet") that the operator can repoint
  (`MODEL_ALIASES`); new assistants and the samples use them. Sonnet 5.5,
  Opus 5.5 and Fable 5.1 are available, and the defaults move to Sonnet
  5.5 (main) and Opus 5.5 (judge). A pinned model that retires keeps
  working as its replacement (EXPLAINER §16, 7a.6).

### Fixed

- A conversation no longer breaks after a redeploy: when the CLI's saved
  session is gone, the next turn carries on from the stored messages.
- Stopping the API leaves time to record running turns as stopped, instead
  of Docker killing it while a watched answer kept a connection open.
- Turn costs use the platform's own prices: the Claude CLI priced models it
  didn't know at Opus 5's rates, over-counting Sonnet 5.5 and Opus 5.5 and
  under-counting Fable 5.1.
- Opus 5.5 and Fable 5.1 no longer fail when an assistant has thinking
  turned off (they always think), and a model the runtime is too old for
  says so instead of "Something went wrong".
- In development the Claude CLI used the developer's own Claude login
  instead of the platform's key; it is now given the key.
- An approval is recorded only while it can still take effect: a decision
  after its expiry is refused (`approval_expired`), two decisions at once
  record one, and an approval whose turn had stopped is marked `cancelled`
  (nothing ran) instead of "approved". Every decision is audited.
- Approvals a crashed process left pending are closed within a minute,
  and a question a crash left unanswered gets a reply saying so
  (EXPLAINER §16, 7a.7).
- A weak connection no longer signs people out: only a refused session
  does. The app keeps the session through a dropped network, a 429 or a
  5xx and retries by itself; a refresh retried after its reply was lost
  gets a new pair instead of revoking the session; refreshes are limited
  per session rather than per address (EXPLAINER §16, 7a.8).

### Security

- Uploaded knowledge files can no longer run as part of the app. The server
  decides each file's type from its contents and refuses anything outside
  PDF, Word, HTML, Markdown and plain text; citation links serve text
  formats (HTML included) as plain text, PDFs inline and Word files as
  downloads, whatever was stored before; the proxy adds `nosniff` and a
  no-scripts policy on the files path (EXPLAINER §16, 7a.1).
- Answers can no longer send data out through images. Images in model
  output are shown as a note naming their host instead of being loaded,
  and the app's content policy allows images only from itself. Links to
  other sites show their host beside the text (EXPLAINER §16, 7a.2).
- Access inside an org is denied by default. Every org route finds the
  caller's membership through one check that also requires a role allowed
  to use the Studio, so a role added later reaches nothing until a route
  is written for it. A test lists every route with who may call it and
  fails on any route missing from the list (EXPLAINER §16, 7a.3).
- Admins can read other people's conversations but no longer post into
  them, stop them, rename them or archive them: a message runs the agent as
  the conversation's owner. The chat page shows anyone but the person who
  started a conversation a read-only view (EXPLAINER §16, 7a.4).
- The Claude CLI's session transcripts are removed with their conversation
  (on archive and assistant delete, plus a daily sweep), and kept in one
  known place instead of the server user's home (EXPLAINER §16, 7a.5).

## [1.0.0] - Unreleased

The first release. The date is set when the release is tagged.

### Added

#### Building assistants

- One configuration (`AssistantConfig`) behind every way of editing it: a
  canvas drawn as a line diagram, form panels, a guided setup, and the
  compiled config to read. The canvas compiles to the config and back
  without loss (ADR 0004; §3.1–3.6).
- Validation on every save, shown on the canvas and in a Problems list,
  with a one-click fix for most problems (§11.11).
- Published versions that never change, a history, and a comparison of
  any two: drawn as one pipeline with what was added, removed and changed
  marked, then listed setting by setting (§3.7, §7.5, §12.8).
- AI help: a system prompt written from a description, and a whole
  starter pipeline recommended from one (§11.5, §11.6).
- Three sample assistants to start from, each with its own eval suite:
  Store support desk, Team notebook, Research desk (§12.7).
- The canvas works from the keyboard: stations in reading order,
  connecting and disconnecting from each station's drawer, arrow keys to
  move, announcements for a screen reader (§12.8).

#### Knowledge bases

- Files (PDF, Word, HTML, Markdown, text), web pages and pasted text,
  indexed in the background (§4.1–4.5).
- Hybrid search: vectors in pgvector, Postgres full-text, fused with RRF
  and reranked; contextual retrieval; a retrieval subagent (§4.6, §4.11).
- Answers cite their sources, and the chat shows the passage behind each
  citation (§4.8).
- Voyage embeddings and reranking, or a local model that needs no key and
  no network (`RAG_OFFLINE=1`; §4.4).

#### Databases

- Postgres, MySQL, SQLite and MongoDB, read-only by default, behind a SQL
  guard that parses every statement (§5.3–5.6, §6.1).
- Credentials encrypted at rest with a key that is never stored with the
  data, and can be rotated (§5.2).
- Every write waits for a person, who sees the exact statement (§5.7).

#### Tools and MCP

- Web search, HTTP requests to listed sites (behind an SSRF guard), a
  calculator, and the date and time (§10.1, §10.2).
- Remote MCP servers, and local-command ones in a sandboxed runner with
  no route out by default; per-tool approval; a catalog of well-known
  servers (§10.5–10.12).

#### The agent

- Runs on the Claude Agent SDK, with streaming, Stop, and resume of long
  conversations (§3.8, §3.10).
- Answers run on the server, not on the page's connection: reloading,
  switching conversations or losing the network no longer ends one; the
  page watches it again on return, and several conversations can answer
  at once (QOS-01; §13).
- Subagents (retrieval, SQL, research) with their own models and their
  own capabilities, and a router that sets each message's effort
  (§11.1, §11.10).
- Long conversations summarised, titles, and a memory tool that keeps
  notes across conversations (§11.2).
- Guardrails at every step: rules in plain words, prompt-injection
  scanning of documents and tool results, keeping secrets and personal
  data from leaving (§11.3).
- Refusals, a fallback model, and failures that say what they are and
  whether retrying helps (§11.4).

#### Running it

- Usage accounting per turn, budgets per conversation, assistant and
  organisation that warn at 80% and stop at 100%, and a usage page
  (§3.11, §11.7).
- Rate limits per address, person and organisation (§11.8).
- A run trace per answer, shown on the canvas (§11.9).
- Evals: suites of test questions, scored by free checks, retrieval
  labels and a judge model, comparable between versions; and a
  regression gate in CI (§12.1, §12.2).
- Organisations, roles with rank rules, invites, an audit log; sign-up
  open or by invitation (§2.6–2.7, §12.6).
- `/metrics` for Prometheus, a Grafana dashboard and alert rules;
  OpenTelemetry and Langfuse tracing (§12.4).
- A production compose file: one way in (Caddy, TLS), no default
  secrets, datastores off the internet, locked-down containers; and a
  preflight that refuses a bad configuration in readable lines (§12.6).
- Backup, verify and restore scripts, an upgrade runbook, an operator
  guide and a user guide (§12.7).

#### The landing page

- A landing page that shows the product in its own terms: one support
  assistant drawn as the builder draws it, with a message travelling it
  on a loop; sections that draw themselves in as they scroll into view;
  a layout for phones, where signing in is left to a computer;
  the four steps from a blank canvas to
  a published assistant; what an assistant can use; approvals and Run
  details; and what self-hosting takes (§14).

### Security

A dedicated review before release (§12.3; `docs/THREAT_MODEL.md`)
found and fixed, among others:

- SQL functions that read files or other databases slipped past a
  list of banned names; the guard now bans whole families.
- An SQLite connection could open the platform's own database file.
- An admin could invite someone as owner; anyone in a conversation could
  approve its tool calls.
- Logs and approval cards could print secrets; redaction now works by
  position and by key, tracebacks included.
- Outbound requests: IPv6 forms of private addresses, redirects across
  origins, and compressed responses were tightened.

`pyjwt` was upgraded past ten published advisories.

### Fixed before release

Problems found by load testing, live checks and manual testing, each
described where it was fixed:

- Every open chat held two database connections for its whole length;
  32 chats at once failed 98% of turns (§12.5).
- A knowledge base asked for 100 candidates got 40, and a tenant filter
  could shorten that further (§12.5).
- Keyboard moves on the canvas were not saved (§12.8).
- The web image ignored `NEXT_PUBLIC_API_URL` set at run time (§12.6).
- Shell scripts with Windows line endings would stop the API container
  from starting (§12.6).

### Known limitations

Listed with what to do about each in the release notes. In short: one
API process measured, against a stand-in model; images for amd64 only;
members cannot be removed; local-command MCP servers share one sandbox.
