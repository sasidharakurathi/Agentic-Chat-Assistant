# Assistant Studio 1.0.0

Release notes, prepared for the team to review before tagging. The full
list of what is in it is [`CHANGELOG.md`](../CHANGELOG.md); how each piece
works is [`EXPLAINER.md`](EXPLAINER.md).

## What it is

A self-hosted platform for building chat assistants that answer from your
own documents and databases, use tools and MCP servers, and ask a person
before changing anything. You build an assistant by drawing what it may
use on a canvas, try it in a chat, measure it with test questions, and
publish it. It runs on the Claude Agent SDK, in Docker, on one machine.

## Highlights

- **A canvas that is the configuration.** What is drawn is what the
  assistant can do; a problem shows on the station it belongs to, with a
  fix. Versions are compared as one drawing.
- **Answers that don't depend on the page.** An answer keeps being
  written if the page is reloaded, closed or switched away from; coming
  back picks it up, and several conversations can answer at once.
- **Answers you can check.** Hybrid search over your documents, numbered
  citations, and the passage behind each one.
- **Nothing changes without a person.** Database writes, HTTP requests
  and MCP tools that change things stop and show exactly what will run.
- **Costs you set.** Budgets per conversation, assistant and
  organisation; usage per turn, model and assistant; rate limits.
- **Measured, not hoped.** Eval suites with free checks, retrieval
  scoring and a judge; a regression gate in CI; three samples that pass
  their own suites.
- **Ready to run.** A production compose file with TLS and no default
  secrets, a preflight that explains what is wrong, metrics and alerts,
  tested backups, an upgrade runbook, and guides for operators and
  builders.

## Installing

[`OPERATIONS.md`](OPERATIONS.md) §2. In short: fill in
`deploy/production.env.example` as `.env.production`, keep a copy of
`APP_KEK` somewhere else, then
`docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build`.

There is nothing to upgrade from: this is the first release. From here
on, upgrades follow [`OPERATIONS.md`](OPERATIONS.md) §7 (back up, verify,
rebuild; migrations run by themselves and only go forward).

**Requirements:** a Linux host with Docker and the Compose plugin; ports
80 and 443; an Anthropic API key; optionally a Voyage key (or the local
model, `RAG_OFFLINE=1`). For trying it without spending anything,
`AGENT_DRIVER=fake` answers with a stand-in model.

## Against the product's own targets

The PRD (§2.3) set these for v1. What was measured, and how:

| Target | Status |
| --- | --- |
| Unapproved risky action executed: 0 | Held by tests: every write, request and gated tool waits for a decision, denies on timeout, and only the person in the conversation or an admin can decide. Mutation-tested. |
| p95 first token < 3.5 s, full answer < 12 s | Platform overhead only: with a stand-in model taking 2 s per turn, one API process with 32 turn slots met both at 32 chats at once (2.4 s, 5.4 s) and missed the first at 64 (4.9 s). **Not measured with the real model.** |
| Retrieval recall@8 ≥ 0.85 | 1.0 on the 15-case regression suite (a small stand-in corpus), and 5 of 5 with recall 1.0 on the Store support sample with the local model. **Not measured on a real customer corpus with Voyage.** |
| Groundedness ≥ 4.2 (judge) | **Not measured**: the judge runs only on the real model, and no real-model runs were made while building. |
| Time to first grounded answer < 15 min | **Not timed.** The Store support sample answers with citations within a minute of being created. |
| Cold start < 5 min | **Not timed** on a fresh machine. |
| WCAG 2.1 AA for chat and config | Designed for it (contrast checked, full keyboard use of the canvas, names and announcements for screen readers). **Not audited, and not tried with a real screen reader.** |

The first thing to do after tagging is to run the three samples' eval
suites, and a load test, against the real model: that turns the bold
lines above into numbers.

## Known limitations

Each is described, with what it means for an operator, in
[`THREAT_MODEL.md`](THREAT_MODEL.md) §5 or the section named.

**Security and access**

- Local-command MCP servers share one sandbox and can read each other's
  environment. Only admins can add them; add only servers you trust.
- Members cannot be removed and invites cannot be revoked; a member can
  only be lowered to the lowest role.
- No email verification. Use `REGISTRATION=invite` on a reachable server
  (the production default).
- Sign-in tokens are kept in the browser's local storage, and logging out
  leaves the 15-minute access token valid until it expires.
- An MCP server's own "read-only" hint is trusted when a builder sets its
  approval to automatic, and re-discovering a server changes what
  published versions offer.

**Operations**

- Images are built for amd64 only (the plan asked for arm64 too).
- Measured as one API process; more than one was not load-tested.
- The production stack was validated (`docker compose config`, the
  preflight, the backup scripts against the development stack) but not
  brought up end to end, and the Caddy configuration has not been loaded
  by Caddy. [`MANUAL_TESTING.md`](MANUAL_TESTING.md) §12.6 is the
  procedure.
- The separate `minio/mc` image could not be pulled while this was
  built; the stack no longer needs it. Pin `MINIO_IMAGE` to an image you
  have tested.
- Backups do not include `APP_KEK` (on purpose) or Redis (a queue, and
  answers being written).
- An answer belongs to the API process running it: restarting the API
  stops the answers in progress (each is recorded as stopped, and anyone
  watching is told).

**Product**

- No page for the audit log (there is an API), and no one-click restore
  of an old version.
- Tidy up puts every station of one kind in a single column, which is
  tall for a hundred data sources.
- Anthropic models only; OAuth connectors (Drive, Notion, Slack and so
  on) are post-v1, as planned (PRD §2.2).
- `vitest` (development only) has two moderate advisories whose fix is a
  breaking upgrade.

## Verification of this release

`scripts/check.ps1`, everything CI runs, on 2026-10-01 after the last change (detached turns), all green:

| Step | Result |
| --- | --- |
| ruff, format | clean (353 files) |
| mypy, Windows and Linux | clean (211 files each) |
| API unit tests | 1386 passed, 2 skipped (the MCP jail's POSIX-only tests, on Windows) |
| API integration tests (Postgres, pgvector, Redis, MinIO, MySQL, Mongo) | 73 passed |
| Migrations against the models | no drift |
| Generated API types | match the OpenAPI schema |
| Web typecheck, lint, unit tests | clean; 217 passed |
| Prettier | clean |

The first run of that check failed: a header-redaction rule from the
security pass removed more of a log line than the credential. It was
fixed and covered by six new cases before the run above (EXPLAINER
§12.9).

Each task was also mutation-tested (its fixes undone one at a time to
prove a test notices) and, where the browser could show it, checked live;
the explainer records both per task.

## Credits

Built by a two-person team with Claude Code, task by task against
[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md). Every task's tests,
mutation checks and live checks are recorded in the explainer.
