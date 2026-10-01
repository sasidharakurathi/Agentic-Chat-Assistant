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

Nothing yet.

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
