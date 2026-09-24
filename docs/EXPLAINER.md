# Assistant Studio — Explainer (learn-as-we-build)

A pin-to-pin walkthrough of the project: the concepts, the "why" behind each
choice, and what every piece of code does. Grows one section per phase. Read it
top to bottom the first time; after that, jump to the phase you're working on.

- Companion docs: [`PRD.md`](PRD.md) (what & why), [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) (how & when), [`adr/`](adr/) (decisions).

---

## 0. The one-paragraph mental model

A user builds an **Assistant** — a chat bot wired to *their* documents, databases,
tools, and MCP servers. They build it three interchangeable ways: a **visual
node-graph**, a **wizard**, or **form panels**. All three produce one JSON object:
the **`AssistantConfig`**. When someone chats with the Assistant, the **Agent
Runtime** reads that config, assembles the right toolset, and runs the
**Anthropic Agent SDK** loop. Everything else — RAG, SQL generation, approvals,
budgets, streaming — hangs off those two nouns: **config** and **runtime**.

```
        AUTHORING                     STORAGE                 EXECUTION
  ┌───────────────────┐        ┌──────────────────┐     ┌──────────────────┐
  │ canvas (React     │        │ assistant_versions│     │  AgentRuntime    │
  │  Flow) ───────────┼─┐      │  - graph  (jsonb) │     │  builds SDK opts │
  │ wizard            │ ├─────►│  - config (jsonb) │────►│  from config,    │
  │ form panels ──────┼─┘      │  (immutable snap) │     │  runs the loop,  │
  └───────────────────┘        └──────────────────┘     │  streams (SSE)   │
        graph  ──compile──►  config   ◄──project──         └──────────────────┘
```

Two golden rules:

1. **`AssistantConfig` is the only thing the runtime understands.** The graph is
   an *authoring* representation that gets **compiled** to a config. (ADR 0004.)
2. **The agent decides tool order at runtime** (it's an LLM loop, not a fixed
   pipeline). Graph edges say *what is available*, not *what runs when*.

---

## 1. Tech stack — what and why

| Layer | Choice | Why this one |
|---|---|---|
| Agent loop | **Anthropic Agent SDK (Python)** | It *is* Claude Code as a library: gives us the tool-use loop, context management, MCP support, subagents, hooks, permission callbacks, sessions — for free. Python/TS only, and we want one backend language. |
| Backend | **FastAPI + async SQLAlchemy 2.0 + Pydantic v2** | Async end-to-end (agent turns are I/O-bound: model calls, DB, retrieval). Pydantic gives us typed request/response models *and* the config schema *and* JSON Schema for the UI, from one definition. |
| DB | **Postgres 16 + `pgvector`** | One datastore for app data *and* vector search in v1. `pgvector` does approximate-nearest-neighbour; Postgres full-text search does the keyword half of hybrid retrieval. |
| Queue | **Redis + Arq** | Document ingestion, eval runs, and long-thread summarization are background jobs — you don't make the user wait for a 10k-page PDF to embed. |
| Object storage | **MinIO** (S3 API) | Uploaded files. Swap for real S3 by changing env vars. |
| Frontend | **Next.js 15 + React 19 + Tailwind v4** | App Router for server components; Tailwind v4 configures theme in CSS (no JS config file). |
| Canvas | **React Flow (`@xyflow/react`)** | The de-facto node-graph library — pan/zoom, typed connection handles, custom node components. |
| Embeddings / rerank | **Voyage AI** (+ local BGE fallback) | Strong retrieval quality; Anthropic-recommended. The fallback keeps "fully offline self-host" real. |
| Auth | **Custom JWT** (access + rotating refresh), **argon2id** | No external identity provider needed for self-host. argon2id is the current best-practice password hash. |
| Observability | **structlog + OpenTelemetry + Langfuse** (optional) | JSON logs you can grep; OTel traces for latency; Langfuse for LLM-specific traces (prompt, tokens, cost). |

Everything runs from `docker compose up`. On this dev box we also learned: there's
a **local Postgres on `:5432`**, so the compose stack publishes on **`:45432`**
(and Redis on `:46379`) to avoid the collision. Those ports were originally
`55432`/`56379` and moved below 49152 after a reboot: Windows reserves random blocks
of its dynamic port range (49152–65535) for Hyper-V at boot, and one such block
swallowed `55432`, so Postgres could not start at all.

---

## 2. Phase 0 — Foundations

**Goal:** a running skeleton you can log into, with the plumbing every later phase
needs: config, logging, DB migrations, auth, roles, tests, CI, Docker.

### 2.1 The monorepo

```
apps/api        FastAPI backend        (Python)
apps/web        Next.js frontend       (TypeScript)
packages/shared types shared by both
docker/         Dockerfiles
docs/           you are here
scripts/*.ps1   Windows entry points (PowerShell 5.1 has no && so the README
                one-liners don't work — these scripts do)
```

`just` (a task runner) and a mirrored `Makefile` exist, but the dev box has
neither `just` nor `make`, so the **PowerShell scripts are the real entry
points**: `setup.ps1`, `migrate.ps1`, `seed.ps1`, `dev-api.ps1`, `dev-web.ps1`,
`check.ps1`.

> **Concept — why `.ps1` files are ASCII-only:** Windows PowerShell 5.1 reads a
> script using the system's legacy codepage unless the file has a UTF-8 BOM. A
> stray "—" or "→" becomes garbage bytes and the script fails to parse. So the
> scripts use plain ASCII.
>
> **Concept — `$ErrorActionPreference` and native stderr:** in PS 5.1, a native
> program writing to stderr (Alembic and uvicorn log there) is turned into an
> error record. With `$ErrorActionPreference = "Stop"` that *aborts the script*
> even when the program exited 0. So our scripts don't set that; instead
> `Invoke-Native` checks the real `$LASTEXITCODE`.

### 2.2 Settings — `app/config.py`

One `Settings` class (`pydantic-settings`). Every tunable — DB URL, JWT secret,
model keys, ports — is a field with a default. It reads `.env`. Nothing else in
the codebase touches `os.environ`.

> **Concept — fail fast in production.** `validate_production_secrets()` runs
> after the settings load. In `dev` it *generates* an ephemeral `JWT_SECRET` if
> you left the placeholder (with a warning). In `production` it **refuses to
> boot** if `JWT_SECRET`, `APP_KEK`, or `ANTHROPIC_API_KEY` are missing/weak. A
> server that starts with an insecure default is worse than one that won't start.

`setup.ps1` bakes a real random `JWT_SECRET` + `APP_KEK` into your `.env` so dev
tokens survive restarts and there's no warning spam.

### 2.3 Structured logging — `app/logging.py`

`structlog` emits either coloured console lines (dev) or one JSON object per line
(prod). A `RequestContextMiddleware` puts a random `request_id` into a
`ContextVar` at the start of every HTTP request; every log line emitted while
handling that request automatically carries it. When something breaks in prod you
grep one id and see the whole request.

### 2.4 Database layer — `app/db/`

- **`base.py`** — `Base` (the SQLAlchemy declarative base) with a *constraint
  naming convention* so index/FK names are predictable across Postgres and
  SQLite. Two mixins: `UUIDPrimaryKeyMixin` (uuid PK), `TimestampMixin`
  (`created_at` / `updated_at`).
- **`types.py` — `TZDateTime`.** *Concept:* SQLite has no timezone-aware
  timestamp type; it hands back naive `datetime`s, which then blow up when you
  compare them to `datetime.now(UTC)`. `TZDateTime` is a `TypeDecorator` that
  forces every datetime to timezone-aware UTC on the way in *and* out, on every
  backend. Tests (SQLite) and prod (Postgres `timestamptz`) now behave the same.
- **`session.py`** — one async engine, a session factory, and `get_session()`, a
  FastAPI dependency that yields a session and **commits on success / rolls back
  on exception**.

> **Concept — the auto-rollback gotcha (we hit this).** Because `get_session`
> rolls back on *any* exception, a security-critical write done right before
> raising an error (revoking a stolen refresh-token family) would be *undone*.
> The fix: in `rotate_refresh_token`, call `await session.commit()` explicitly
> *before* raising the 401.

### 2.5 Migrations — Alembic

The DB schema is versioned. Each change is one migration file under
`app/db/migrations/versions/`. `alembic.ini` uses `%(here)s` so it runs from any
directory: `alembic -c apps/api/alembic.ini upgrade head`.

`env.py` is wired for the **async** engine and pulls the URL from
`app.config.settings` (not from the ini). A `render_item` hook makes migrations
render our `TZDateTime` as plain `sa.DateTime(timezone=True)`, so migration files
carry no dependency on app code.

### 2.6 The identity model — `app/models/`

```
Organization ──< Membership >── User          (many-to-many, with a role)
Organization ──< Invite                        (pending join, token hashed)
Organization ──< ApiToken                      (scoped, hashed, revocable)
User        ──< RefreshToken                   (rotation + reuse detection)
AuditLog                                        (append-only security log)
```

> **Concept — tenancy.** *Organization* is the isolation boundary. Every
> tenant-scoped row carries `org_id`; every query filters by it. A user belongs
> to one or more orgs via `Membership`, each with a `role`.
>
> **Concept — RBAC.** `MemberRole` is `owner > admin > member`. The dependency
> `require_role(MemberRole.admin)` (in `app/api/deps.py`) resolves the caller's
> membership for the `{org_id}` in the path and 403s if their role is too low.
> The last owner of an org can't be demoted (you'd lock everyone out).

### 2.7 Auth — `app/security/` + `app/services/auth.py`

- **Passwords:** `argon2id` via `argon2-cffi`. `verify_password` also reports if
  the hash needs upgrading (params changed) and we re-hash on next login.
- **JWTs:** two kinds.
  - *Access token* — 15 min, stateless. Carries `sub` (user id), `jti`, `exp`.
  - *Refresh token* — 30 days, **stateful**. Every issued one is a row in
    `refresh_tokens` with a `jti` and a `family_id`.

> **Concept — refresh-token rotation with reuse detection.** Long-lived tokens
> are a theft risk. So: when the client refreshes, we mark the presented token
> `used_at` and issue a *new* refresh token in the **same family**. If a token
> that's already `used` (or unknown) is presented again — that's a replay, almost
> certainly a stolen token — we **revoke the entire family**, logging everyone on
> that chain out. The legitimate user just logs in again; the attacker's stolen
> token is now dead. `tests/test_auth.py::test_refresh_rotation_and_reuse_detection`
> pins this behaviour.

### 2.8 Error envelope — `app/api/errors.py`

Every handled error renders as:

```json
{ "error": { "code": "forbidden", "message": "…", "details": {…} },
  "request_id": "…" }
```

`AppError` subclasses (`NotFound`, `Forbidden`, `Conflict`, …) carry a status
code + a stable machine `code` the frontend switches on. A catch-all handler
turns anything unhandled into a 500 with the request id (and logs the traceback).

### 2.9 Tests

`pytest` (async, `asyncio_mode=auto`). `conftest.py` points `DATABASE_URL` at a
throwaway **SQLite** file *before* `app` imports, and rebuilds the schema per
test. So the suite runs with **no Docker**. 14 tests: health/readiness, the auth
flows, org CRUD, invites, RBAC, and one that runs `alembic upgrade head` and
checks every table exists.

### 2.10 Frontend skeleton

Next.js 15 App Router. `lib/api.ts` is a typed `fetch` wrapper + a localStorage
token store. Pages: marketing home, `(auth)/login`, `(auth)/register`,
`dashboard` (lists your orgs, "assistants coming in Phase 1"). Tailwind v4 theme
tokens live in `app/globals.css` and cover light + dark.

### 2.11 CI & Docker

- `.github/workflows/ci.yml` — two jobs. `api`: ruff + mypy + pytest + pip-audit.
  `web`: tsc + eslint + `next build` + npm audit.
- `docker-compose.yml` — `postgres` (pgvector), `redis`, `minio` always; `api` +
  `web` behind `--profile apps`. `docker/api.Dockerfile` also installs Node +
  (later) the Claude Code CLI, because the Agent SDK spawns it as a subprocess.

**Phase 0 exit state:** register → log in → see an empty dashboard; `check.ps1`
green; full stack verified against real Postgres.

---

## 3. Phase 1 — Config schema + graph model  *(complete)*

**Goal of the two pieces built so far:** nail the data model *before* building the
canvas UI or the runtime on top of it. The plan flags the **graph ⇄ config
round-trip** as the #1 risk to de-risk early (ADR 0004).

### 3.1 `AssistantConfig` — `app/schemas/assistant_config.py`

The executable contract. A tree of Pydantic models, all `extra="forbid"` (an
unknown key is an error, not silently ignored — config drift must be loud).

```
AssistantConfig
├─ schema_version: 1
├─ models: ModelRoles { router, main, subagent, judge : ModelSpec }
│                       ModelSpec { model, effort, thinking, max_turns, max_budget_usd }
├─ system_prompt: str
├─ guardrails: Guardrails { rules[], pii_redaction, injection_scan, refusal_fallback, … }
├─ rag: RagConfig { enabled, embedder, reranker, chunking, contextual_retrieval,
│                   retrieval: RagRetrieval { hybrid, top_k_*, rrf_k, rerank_top_n, … },
│                   citations, source_ids[] }
├─ databases: [ DatabaseRef { connection_id, nl2sql, expose_write } ]
├─ tools: ToolsConfig { web_search, http_request, calculator, datetime }
├─ mcp_servers: [ uuid ]
├─ subagents: SubagentsConfig { retrieval, sql, research : bool }
├─ memory: MemoryConfig { persist_history, summarize_after_tokens, memory_tool, auto_title }
└─ approval_policy: ApprovalPolicy { db_write, db_ddl, http_non_get, mcp_default,
                                     file_write:"deny", shell:"deny" }
```

**Validators — where "invalid" is caught:**

| Rule | Why |
|---|---|
| `model` ∈ `ALLOWED_MODELS` (in `app/agent/models.py`) | one place to change model ids; reject `gpt-4o` etc. |
| `rerank_top_n ≤ min(top_k_dense, top_k_sparse)` | you can't rerank more candidates than you retrieved |
| `max_budget_usd > 0` | a zero budget is a bug, not a config |
| no duplicate `connection_id` / `mcp_server` id | ambiguous wiring |
| `file_write` / `shell` locked to `"deny"` | those tools are never exposed to tenants in v1 (ADR 0003) |

**Canonicalization.** Order doesn't matter for `databases`, `mcp_servers`,
`rag.source_ids` — so a model validator **sorts** them. Two configs authored in
different orders now compare `==`. This is what makes the round-trip test below
possible.

**`config_json_schema()`** returns `AssistantConfig.model_json_schema()` — a
standard JSON Schema the frontend will use to render the form panels
automatically. One definition, three consumers (validation, docs, UI).

> **Concept — "node presence = enabled".** `ToggleTool.enabled` defaults to
> `False`. A tool/subagent is "on" **iff** a wired node for it exists in the
> graph. This makes the graph and config a clean bijection (see 3.4).

### 3.2 The graph model — `app/graph/nodes.py`

A `Graph` is `{ schema_version, nodes[], edges[] }`.

- **`Edge`** = `{ source, target }` (node ids). Declarative wiring only.
- **`Node`** — a *discriminated union* on `type`. Each of the 12 node types has
  its own wrapper class (`AgentNode`, `DatabaseNode`, …) with `id`, `position`
  `{x, y}`, and a typed **`data`** payload (`AgentNodeData`, `DatabaseNodeData`,
  …). Pydantic picks the right class from the `type` string and validates `data`
  against the matching model.

The 12 node types and what each contributes to the config:

| Node | data → config |
|---|---|
| `input` / `output` | markers (entry / exit); `output.citations` mirrors `rag.citations` |
| `guardrail` | → `config.guardrails` |
| `router` | overrides `config.models.router` |
| `agent` | → `system_prompt`, `models`, `approval_policy` (exactly one) |
| `memory` | → `config.memory` |
| `knowledge_base` | → `config.rag` (with `enabled=True`) |
| `data_source` | feeds a `knowledge_base` → `rag.source_ids` |
| `database` | → an entry in `config.databases` |
| `tool` | → sets a key in `config.tools` |
| `mcp_server` | → an id in `config.mcp_servers` |
| `subagent` | → sets a bool in `config.subagents` |

**Legal edges** are an explicit allow-list (`_ALLOWED_EDGES` in `validate.py`),
e.g. `data_source → knowledge_base`, `knowledge_base → agent | subagent`,
`agent → output`. Anything else is an error.

### 3.3 `validate_graph()` — `app/graph/validate.py`

Pure, no I/O. Returns `ValidationResult { errors[], warnings[] }`.
**Errors block publish; warnings don't.**

- **Errors:** duplicate node ids, dangling edge (endpoint missing), illegal edge
  type, not exactly one `input` / `agent` / `output`, a cycle, two nodes
  referencing the same connection/tool/mcp/subagent.
- **Warnings:** an orphaned capability node (not wired to the agent), a
  `data_source` not connected to a KB, `input`/`output` not on a path to/from the
  agent, a "bare" subagent (no dedicated capability — fine in v1, it gets a
  standard toolset).

> **What the round-trip spike caught:** the first version made "bare subagent" an
> **error**. But `default_config()` turns on the `retrieval` + `sql` subagents,
> and projecting that to a graph produced subagent nodes with nothing wired into
> them → invalid graph → round-trip test failed. Correct fix: in v1 a subagent is
> a *built-in pattern* (a toggle), so a bare one is just a **warning**. Task 5.10
> (user-composed subagents) may promote it back to an error. This is exactly why
> you build the model + property tests before the UI.

Ownership checks ("does this `connection_id` belong to this assistant?") are
**not** here — they need the DB, so they live in the service layer (task 1.2).

### 3.4 `compile_graph(graph) -> AssistantConfig` — `app/graph/compile.py`

Pure, deterministic, total on valid graphs, **no I/O**. It:

1. validates (raises `GraphCompileError` if there are errors);
2. finds the single `agent` node;
3. for every other node, checks it *reaches* the agent (DFS over edges) — only
   **wired** nodes contribute;
4. projects each wired node onto the matching config section;
5. **sorts** all reference lists so output is byte-stable.

`compile(graph_A) == compile(graph_B)` whenever the graphs are semantically the
same — property-tested in `test_graph_compile.py::test_compile_is_deterministic`.

### 3.5 `project_config(config, existing_graph=None) -> Graph` — `app/graph/project.py`

The reverse. Used when someone edits the **form panels** (which write a `config`)
and we need to refresh the **canvas**.

- Node ids are **deterministic**: `agent`, `guardrail`, `kb`, `db:{connection_id}`,
  `tool:{key}`, `mcp:{id}`, `sub:{role}`, `src:{data_source_id}`, …
- Layout is a fixed left-to-right column layout.
- If `existing_graph` is passed, each node **keeps its previous `{x, y}`** — so
  editing a field in a panel doesn't scramble the canvas you arranged by hand.

### 3.6 The round-trip guarantee (ADR 0004, the Phase-1 spike)

`tests/test_graph_roundtrip.py` proves:

| Property | Meaning |
|---|---|
| `compile(project(default_config())) == default_config()` | the default assistant survives a full trip |
| `compile(project(rich_config)) == rich_config` | so does a fully-loaded one (RAG + 2 DBs + tools + MCP + subagents) |
| `project` is a **fixed point**: `compile(project(compile(project(cfg))))` stops changing after one round | no drift on repeated edits |
| moving a node then re-projecting keeps its position **and** still compiles to the same config | panel edits don't disturb canvas layout |

**Why this matters:** three editing surfaces, one source of truth. If compile and
project didn't agree, switching between the canvas and the panels would silently
change your assistant. The spike makes that impossible before a line of canvas UI
exists.

### 3.7 Assistants + versions — `app/models/assistant.py`, `app/services/assistants.py`, `app/api/routes/assistants.py`  *(done — tasks 1.2 / 1.13)*

**Two tables:**

- **`assistants`** — one row per assistant. Holds the *working copy*:
  `draft_graph` + `draft_config` (both `jsonb`), `status`
  (`draft`/`published`/`archived`), `current_version_id` (a plain uuid column, no
  FK — a real FK here would be circular with `assistant_versions.assistant_id`).
- **`assistant_versions`** — an **immutable** snapshot published from the draft.
  `graph` + `config` frozen, a `version_number` (unique per assistant), a `note`.
  No `updated_at` — versions never change.

> **Concept — jsonb vs JSON.** `app/db/types.py` exports `JSONB = JSON().with_variant(postgresql.JSONB(), "postgresql")`.
> On Postgres you get real `jsonb` (indexable, queryable); on SQLite (tests) it
> degrades to a TEXT-backed `JSON`. One column definition, both backends.

**The draft-sync contract** (the heart of "three surfaces, one config"):

| You call | Server does |
|---|---|
| `PUT /assistants/{id}/draft-graph` (canvas) | store the graph *as-is*; if it **validates**, `compile` it and also update `draft_config`; if not, keep the graph (it's a draft), return the errors, leave `draft_config` stale |
| `PUT /assistants/{id}/draft-config` (form panels) | store the config; `project` it back to a graph, **preserving positions** from the current `draft_graph`; store that too |
| `POST /assistants/{id}/graph:validate` / `graph:compile` | dry runs — validate/compile a graph in the request body, touch nothing |
| `POST /assistants/{id}/versions` (publish) | **require** an error-free draft graph → compile → write an immutable `assistant_versions` row (`version_number = max + 1`) → point `current_version_id` at it → status `published` |
| `GET /assistants/{id}/versions/diff?a=1&b=2` | recursive dotted-path diff of the two configs + a node/edge diff of the two graphs (`app/services/diff.py`) |

> **Concept — invalid drafts are allowed, invalid publishes are not.** You're
> mid-edit, you delete an edge — the canvas must still save. So `save_draft_graph`
> stores *anything*. Only **publish** enforces `validation.ok`. Warnings (an
> orphan node, input not wired) never block anything in v1.

**Access control — two new dependencies in `app/api/deps.py`:**

- **`ActiveMembership`** — for the collection routes (`GET`/`POST /assistants`)
  that have no assistant id yet. Reads the **`X-Org-Id` header**, verifies the
  caller is a member of that org. (The frontend sends the currently-selected org.)
- **`AssistantCtx`** — for `/assistants/{id}/…`. Loads the assistant, then loads
  the caller's membership **in that assistant's org**. No membership → `404`
  (not `403` — we don't reveal that a cross-tenant assistant exists).
  `EditableAssistantCtx` additionally requires the caller be the assistant's
  creator *or* an org admin (PRD FR-3: "members edit their own").

**`test_assistants.py`** pins: create → default 5-node graph compiles clean;
missing `X-Org-Id` → 400; editing the config re-projects the graph (enable
`calculator` → a `tool` node appears); an invalid draft graph is *stored* but not
compiled; publish is rejected when the draft has errors, versions increment, and
the diff surfaces `models.main.model`; a cross-tenant assistant id → 404.

> **Design note we changed here:** `SubagentsConfig` now defaults to **all off**.
> A brand-new assistant with no KB and no DB shouldn't ship with retrieval/sql
> subagents (they'd just raise "bare subagent" warnings). The wizard turns them on
> when you attach the matching capability.

### 3.8 The agent runtime + streaming chat — `app/agent/`, `app/services/chat.py`, `app/api/routes/conversations.py`  *(done — tasks 1.4 / 1.5)*

The piece that actually **runs** an assistant. Built so the whole path is
exercisable **offline, with no API key**.

**The Anthropic Agent SDK.** `claude-agent-sdk` is Claude Code as a Python
library: the tool-use loop, context management, MCP, subagents, hooks, and a
`can_use_tool` permission callback. It *imports* fine anywhere; it only needs the
Claude Code **CLI** (a Node package) + `ANTHROPIC_API_KEY` to actually run a turn.

| File | What it is |
|---|---|
| `agent/models.py` | `ALLOWED_MODELS`, default model per role, rough `$/MTok` rates (cost estimates, not billing). |
| `agent/events.py` | The event union a turn emits: `token`, `thinking`, `tool_call`, `tool_result`, `approval_required`, `usage`, `error`, `done`. `sse_frame(ev)` renders `data: {json}\n\n`. |
| `agent/caps.py` | The **in-process capability tools**. Phase 1: `calculator` (a safe AST-walking evaluator — no `eval`, exponent capped) + `datetime`. Each is a plain `async handler(args)`; `build_caps_server` adapts them to the SDK's `@tool` + `create_sdk_mcp_server`. SDK-free handlers ⇒ unit-testable without the CLI. |
| `agent/options.py` | `build_runtime_spec(config)` → SDK-agnostic `RuntimeSpec` (system prompt, model, effort, the **explicit `allowed_tools` allowlist**). `build_claude_options(...)` → the **locked-down `ClaudeAgentOptions`**: `setting_sources=[]`, `disallowed_tools=[Bash, Write, Edit, …]`, our `can_use_tool`. (ADR 0003.) |
| `agent/approvals.py` | `build_can_use_tool(policy)` — the `can_use_tool` callback. **Deny by default.** `calculator`/`datetime`/`WebSearch` → allow; anything needing a human → deny (the real HITL flow is Phase 3). |
| `agent/driver.py` | **Two drivers, one interface** (`stream(...) -> AsyncIterator[AgentEvent]`, no `done`). `ClaudeSDKDriver` runs the real loop and maps SDK blocks → our events. `FakeDriver` is **deterministic + offline**: answers arithmetic via the real calculator handler, else echoes the prompt. `get_driver()`: `APP_ENV=test` or no key → fake; else real. (`AGENT_DRIVER` overrides.) |
| `agent/runtime.py` | `Turn` — streams a driver's events through, **accumulates** a `TurnOutcome`, and **enforces the per-conversation budget mid-stream** (over → `budget_exceeded` + stop). Handles client-disconnect cancellation → status `aborted`. |

**Data model — `app/models/conversation.py`:** `conversations` (pins an
`assistant_version_id` or null = draft; tracks `cost_usd` + `token_usage`),
`messages` (role, content, `blocks`), `runs` (one row per turn).

> **Concept — message ordering on SQLite.** `messages.created_at` gets a
> **Python-side** default (`datetime.now(UTC)`, µs precision) *and* a
> `server_default`. SQLite's `CURRENT_TIMESTAMP` is 1-second-coarse, so without
> the Python default a user message and its answer could sort in either order.

**`app/services/chat.py` — `run_message`:**

1. Load conversation + config (pinned version, or the draft).
2. **Budget precheck** — over the cap → emit `budget_exceeded`, stop.
3. Persist the user message and **`commit()` immediately** — survives a
   mid-stream client disconnect.
4. Run the `Turn`, yielding every event.
5. Persist the assistant message + a `run` row; bump `cost_usd` /
   `token_usage` / `last_message_at`; `commit()`.
6. Emit `done` with the new ids.

**`app/api/routes/conversations.py`:** `POST /conversations/{id}/messages` is a
**`StreamingResponse`** of `text/event-stream`. Its generator opens its **own**
DB session (so it can `commit()` at checkpoints, unlike the request dependency),
checks `request.is_disconnected()` each loop, and always closes with an `error`
frame on failure. `ConversationCtx` (new dep) — cross-tenant → 404.

**`test_chat.py`** parses real SSE frames (FakeDriver): a basic turn streams
`token` + `usage` + `done` and persists a user/assistant pair with cost tracked;
the calculator tool yields `tool_call` + `tool_result` (`= 42.0`); a tiny
`max_budget_usd` aborts the turn and blocks the follow-up.

> **Live-verified against Postgres:** "calculate 19 * 19" streamed
> `tool_call → tool_result (= 361.0) → token → usage → done`; both messages
> persisted; `cost_usd` recorded. The real `ClaudeSDKDriver` is written but
> unverified — set `ANTHROPIC_API_KEY` + install `@anthropic-ai/claude-code` and
> the same endpoint calls the real model.

### 3.9 The frontend — `apps/web/`  *(done — tasks 1.3 / 1.6 / 1.14)*

Next.js 15 App Router. The whole vertical slice now works in a browser:
**login → assistants → create → canvas → edit → chat (SSE)**.

**Client plumbing (`lib/`):**

- **`api.ts`** — a typed `fetch` wrapper. Tokens in `localStorage`; the active org
  id is attached as `X-Org-Id` on every call that needs it. Plus `streamMessage()`
  — reads the SSE `ReadableStream`, splits on `\n\n`, parses each `data:` frame
  into a typed `ChatEvent`.
- **`auth.tsx`** — a React context: on mount it loads `/me` + `/orgs`, picks the
  active org, and exposes `login` / `register` / `logout`. `(app)/layout.tsx`
  redirects to `/login` when there's no user.

**The build page (`(app)/assistants/[id]/build`):** three tabs over one draft.

- **Canvas** — `@xyflow/react`. `graph-sync.ts` maps a `Graph` ↔ React Flow
  nodes/edges. One custom node type (`StudioNode`) renders per `data.node.type`
  with typed source/target handles; error nodes get a red border. Click a node →
  **`NodeDrawer`** shows the matching panel (`AgentPanel` = prompt + model +
  effort; `GuardrailsPanel` = rules + toggles; `MemoryPanel`). Every edit calls
  `PUT /draft-graph`; the server's response (recompiled config + validation)
  updates the UI. Dragging a node persists its position the same way.
- **Panels** — the same editors as a plain form; the **Tools** panel writes
  `PUT /draft-config` (toggle the calculator → a `tool` node appears on the
  canvas, proving the round-trip).
- **Config JSON** — the live compiled `AssistantConfig`.

Header shows the validation badge (errors block **Publish**) and a link to Chat.

**The chat page (`(app)/assistants/[id]/chat`):** conversation list + `ChatThread`.
`ChatThread` loads persisted messages, then `streamMessage` drives a live
assistant bubble from `token` events with expandable `ToolCallCard`s from
`tool_call` / `tool_result`. On `done` it reloads from the server (authoritative).

> **Bug caught by the browser test:** `POST /conversations` returned 201 but an
> immediate `GET` 404'd for a few seconds. Cause: `get_session` commits *after*
> the response is sent, so a create-then-read from the SPA raced the commit. Fix:
> the mutating service functions (`create_conversation`, assistant `create` /
> `update_meta` / `save_draft_*` / `publish`) now `await session.commit()`
> explicitly. Verified: the same `GET` now returns 200 ~40 ms after the `POST`.

### 3.10 SDK session resume — Redis fast path, Postgres source of truth  *(done — task 1.7)*

**The concept — what "resume" even means here.** The Agent SDK's `ClaudeSDKClient`
keeps conversation state (prior turns, tool results) on the CLI side, addressed by
a `session_id`. Pass `options.resume = <that id>` on the *next* turn and the CLI
picks the conversation back up instead of starting fresh — so the backend doesn't
need to replay the whole message history into the prompt every turn. The plumbing
to *pass* a session id into the driver already existed (`chat.py` read
`conv.sdk_session_id` into `Turn`); what was missing was ever **capturing** the id
the SDK hands back and writing it anywhere. So every turn "resumed" from nothing,
silently — the column was always `NULL`. That's the bug this task fixed.

**Where the id comes from:** the SDK's `ResultMessage` (emitted once, at the end
of a turn) carries `session_id`. `ClaudeSDKDriver` now folds it into the
`UsageEvent` it already yields (`app/agent/driver.py`) — one event, no new event
kind. `Turn._absorb` (`app/agent/runtime.py`) picks it up onto
`TurnOutcome.sdk_session_id`. `FakeDriver` plays along for offline testing: it
echoes back whatever `session_id` it was given, or mints a `fake-<hex>` one if
this is the first turn — so a two-turn test can assert continuity without the
real SDK or an API key.

**Where it's stored — two tiers, one writer:**

- **Postgres** (`Conversation.sdk_session_id`, already existed as a column) is the
  **durable source of truth**. `chat.run_message` writes it in the same
  transaction as the rest of the turn's bookkeeping (cost, token usage,
  `last_message_at`) — so it can never desync from "did this turn actually
  happen."
- **Redis** (`app/agent/session_store.py`, new) is a **fast-path cache** in front
  of it — `conversation_id → sdk_session_id`, 60-day TTL. `chat.run_message` reads
  Redis first at the top of a turn (saves nothing today, since the conversation
  row is already loaded either way, but keeps the two tiers in the same shape the
  plan called for — see `docs/IMPLEMENTATION_PLAN.md` line 500).

**The important design choice: Redis is disposable.** Every `session_store.get` /
`set` call is wrapped in `try/except RedisError` and logs a warning instead of
raising. If Redis is down, cold, or evicted, a turn just falls back to the
Postgres value already on the loaded `Conversation` row — resume degrades to
"exactly as good as the DB row," never to "the whole turn fails." This mirrors
the `FakeDriver`/`ClaudeSDKDriver` split from 1.4/1.5: the feature works whether
or not the optional infra is present.

**`app/db/redis.py`** — a `redis.asyncio.Redis` client, `@lru_cache`d exactly like
`get_engine()`/`get_sessionmaker()` in `db/session.py`. Nothing touches the
network at import time, so tests never need Redis running.

> **Live-verified:** with the Docker Redis container up, two real HTTP turns
> against the same conversation both reported
> `sdk_session_id: "fake-18a31684141a"` in their `usage` SSE frame — same id both
> times, proving the second turn actually received what the first one produced.
> `docker exec redis redis-cli GET sdk-session:<conversation-id>` returned that
> same value, confirming the cache write landed for real, not just the Postgres
> fallback. `test_session_store.py` (new) proves the degrade-gracefully path by
> pointing the client at an unreachable address and asserting `get`/`set` don't
> raise; `test_chat.py::test_session_id_resumes_across_turns` proves the
> two-turn continuity offline via `FakeDriver`.

### 3.11 Usage accounting + the `usage_events` ledger  *(done — task 1.8)*

**Why a new table when `runs` already records cost/tokens per turn?** `runs`
is keyed for *tracing* — one row per conversation turn, mostly interesting
alongside its `message_id`. `usage_events` (new, `app/models/usage.py`) is a
flatter, append-only **ledger** shaped for *rollups*: `org_id` and
`assistant_id` are indexed columns (not something you'd join through
`conversations` to get), and `kind` (`llm | embedding | rerank | tool`) leaves
room for Phase 2's embedding calls and Phase 4's tool costs to land in the
same table without a schema change. `chat.run_message` writes one row
(`kind="llm"`) right alongside the `Run` it already creates — same
transaction, so the two can never drift apart.

**The rollup — `GET /orgs/{id}/usage?group_by=assistant|model&from=&to=`**
(`app/services/usage.py`, wired into `app/api/routes/orgs.py`): one `GROUP BY`
query over `usage_events`, `SUM`ming tokens and cost and `COUNT`ing events,
optionally windowed by `created_at`. Gated by `OrgMembership` (any org member
can view it, same as the existing member-list route) — cross-tenant orgs
404 like everywhere else in the app.

**A latent bug fixed along the way: `num_turns` was always `1`.** While wiring
`usage_events` I re-read `ResultMessage` (the SDK's end-of-turn summary) and
noticed its `num_turns` and `terminal_reason` fields were being read nowhere —
`Run.num_turns` had been hard-defaulting to `1` since 1.4/1.5 regardless of
how many actual tool-calling turns the agent took internally. Fixed the same
way as the `sdk_session_id` gap in 1.7: `UsageEvent` (the SSE event, not the
new DB table — same name, different module, imported explicitly where used)
now carries `num_turns` + `terminal_reason`; `Turn._absorb`
(`app/agent/runtime.py`) applies them, and — since `terminal_reason` can be
literally the string `"max_turns"` — a turn that hits its configured
`max_turns` cap now surfaces as `status=aborted, error="maximum agent turns
reached"` instead of silently reporting `ok`. This closes the "`max_turns`
enforcement + abort" half of 1.8's scope; `max_budget_usd` enforcement was
already done in 1.4/1.5.

> **A test caught a real infra bug too:** the first test run after adding the
> Redis session-store calls (1.7) threw `RuntimeError: Event loop is closed`
> from inside `chat.run_message` — not a `redis.exceptions.RedisError`, so it
> wasn't being caught. Cause: `get_redis()` is `@lru_cache`d process-wide
> (mirroring `get_engine()`), but pytest-asyncio hands each test function its
> own event loop; the cached Redis client's TCP transport was bound to a
> *previous* test's now-closed loop. Fixed by broadening the except clause in
> `session_store.py` to catch any failure (not just `RedisError`) and by
> dropping the cached client (`get_redis.cache_clear()`) whenever a call
> fails, so the next call opens a fresh connection instead of retrying a dead
> one. This matters beyond tests too — a real production connection can drop
> the same way after a network blip or a Redis restart.

> **Live-verified:** created a fresh assistant, sent two turns, then called
> both rollup groupings against the real running server —
> `group_by=assistant` returned one row (`event_count: 2, tokens_out: 23,
> cost_usd: 0.000252`); `group_by=model` returned the same totals keyed by
> `"claude-sonnet-5"`. New tests: `test_agent_runtime.py` (`num_turns` capture
> + the `max_turns` → aborted mapping, as direct unit tests on `Turn._absorb`
> since `FakeDriver` has no multi-tool-call loop to exercise it against)
> and `test_usage.py` (rollup correctness + cross-tenant 404).

### 3.12 The guided setup wizard  *(done — task 1.15 — Phase 1 complete)*

**A scope correction first.** Earlier notes in this doc (and in memory) called
this task "the wizard (`prompt:generate` / `pipeline:recommend`)". That was
imprecise — re-reading `docs/IMPLEMENTATION_PLAN.md`'s actual Phase 1 table,
the wizard item there is **1.15**: *"Wizard stepper → emits a starter graph
(input→guardrail→agent→output) → opens the canvas."* No AI/LLM call involved.
`prompt:generate` (AI writes a system prompt from a use-case description) and
`pipeline:recommend` (AI emits a whole starter graph) are **5.5/5.6** —
Phase 5 polish, deliberately deferred. What got built here is the plain
stepper: 1.15's actual scope.

**Why the wizard needed almost no new backend code.** Every assistant already
gets the "starter graph" for free — `assistants.create()` (1.2) calls
`project_config(default_config())`, and `project_config` (1.12) *always*
emits `input → guardrail → agent → output` (+ `memory → agent`); those five
nodes aren't optional capabilities like tools/DBs, they're the fixed spine of
every pipeline. So "emits a starter graph" was already true before this task.
What the wizard actually adds is a **friendlier front door** to the two calls
that already existed (`POST /assistants`, `PUT draft-config`) — a guided,
step-by-step way to fill in the config's *content* (name, a plain-English
"what should this do?" that becomes `system_prompt`, model/effort, guardrail
rules, memory settings) instead of the bare "type a name" quick-create form.

**`apps/web/app/(app)/assistants/new/page.tsx`** (new) — a 5-step client
component (Basics → Agent → Guardrails → Memory → Review), driven by a
`step: number` index into a `STEPS` tuple. The interesting design choice:
steps 2–4 **reuse `AgentPanel`/`GuardrailsPanel`/`MemoryPanel` verbatim** from
`components/config/panels.tsx` — the exact same form components the build
page's "Panels" tab already used to edit a *live* node's data. Each panel just
needs `{data, onChange}`; the wizard feeds it a local `useState` slice instead
of a node, and `onChange` merges the patch into that slice instead of calling
`PUT /draft-graph`. Same UI, same behavior, zero duplicated form code — only
possible because 1.3's panels were already built decoupled from "where the
data lives."

On **Review → Create assistant**: `assistants.create(name, description)`
first (gets back a fully-valid default config + id), then the wizard's
collected `system_prompt` / `models` / `guardrails` / `memory` are merged on
top of that default and sent as one `PUT /draft-config` — reusing the exact
same save path the Panels tab uses, so there was no new "wizard submit"
endpoint to write or validate. Router then pushes to `/assistants/{id}/build`,
landing on the canvas tab by default, per 1.15's "opens the canvas."

`apps/web/app/(app)/assistants/page.tsx` gained a "Guided setup" link next to
the existing quick-create form — both paths stay available.

> **Live-verified:** ran the wizard end to end in the browser — Basics
> ("Support Triage Bot") → Agent (typed a system prompt, picked
> `claude-haiku-4-5`) → Guardrails (left defaults) → Memory (left defaults) →
> Review (confirmed the exact text/model before creating) → Create. Landed on
> the canvas with the five-node starter graph and the Agent node showing
> `claude-haiku-4-5`. Switched to the Config JSON tab and confirmed
> `system_prompt` and `models.main.model` matched exactly what was typed in
> the wizard, with every other field correctly defaulted — proving the
> wizard→create→draft-config merge round-trips losslessly through the same
> compile/project machinery as every other editing surface.

**Phase 1 is now complete: 1.1–1.8 and 1.12–1.15 are all done.** 1.9
(OTel/Langfuse observability), 1.10 (SDK-native `Stop`/`PostToolUse` hooks —
we get equivalent outcomes today via our own driver/runtime code, just not
through the SDK's hook mechanism), and 1.11 (agent-loop tests against a
replayed SDK transport) are P1 polish items intentionally left for later
rather than blocking Phase 2. Next: a guided manual-testing pass over
everything built so far, then Phase 2 (RAG).

### 3.13 A real design system (not just "make it pretty")

The frontend up to this point used Tailwind's stock shadcn-style tokens
(generic blue primary, plain grayscale) — functional, but visually generic.
Replaced it with an actual **token-driven design system** in
`apps/web/app/globals.css`, so every page inherits the look automatically
instead of needing per-page styling.

**Semantic tokens, not hard-coded colors.** Every primitive (`Button`,
`Card`, `Badge`, `Input`…) already referenced CSS custom properties
(`--primary`, `--muted`, `--border`, …) rather than literal Tailwind colors —
that indirection is what made a *global* palette swap possible by editing one
file. The palette: a warm white/near-black neutral scale (`--background`,
`--foreground`, `--muted`, `--border`, `--card`) plus **tomato red**
(`oklch(0.63 0.19 29)`) as `--primary` — the brand accent used for buttons,
active nav state, links, focus rings. `--destructive` is a distinct
*crimson* (hue 15, not 29) deliberately separated from `--primary` by hue —
both are "red," but close enough in hue that a shared color would make
"delete this" and "the main action button" look like the same affordance.
Light and dark are two `oklch` sets under `:root` / `.dark`; both keep the
same hues, only lightness/chroma shift, so light↔dark is a true reskin, not a
different palette.

**Typography**: `next/font/google` loads `Inter` (body/UI, exposed as
`--font-sans`) and `Source Serif 4` (headings, `--font-serif`) — applied via
`font-serif` on `CardTitle` and the two page-level `<h1>`s. This is the one
deliberately "editorial" touch: dense UI text (labels, buttons, node text)
stays sans for legibility; only titles get the serif, so it reads as
intentional hierarchy rather than decoration everywhere.

**The canvas accent palette — designed, not grabbed.** `StudioNode.tsx`
originally picked a different *named* Tailwind color per node type
(`amber-500`, `violet-500`, `emerald-500`, `sky-500`, `teal-500`,
`fuchsia-500`, `indigo-500`) — each from Tailwind's own scale, which isn't
perceptually uniform, so the set looked arbitrary next to the new palette.
Replaced with 8 `--node-*` tokens: hues rotated exactly 45° apart around the
wheel starting from `--primary`'s 29°, all held at matched lightness/chroma
per light/dark mode — one deliberate categorical family instead of assorted
defaults, exposed as real Tailwind utilities (`border-node-guardrail`, etc.)
via `@theme inline` so they're usable the same way `border-primary` is.

> **A real bug this caught**: `Canvas.tsx` had `colorMode="system"` on the
> React Flow root — React Flow's own dark-mode switch, which reads the OS
> `prefers-color-scheme` media query *directly*, completely independent of
> the app's own `next-themes` state. Toggling the app to light mode left the
> canvas background dark because the two "theme" concepts were never wired
> together. Fixed by reading `useTheme().resolvedTheme` from `next-themes`
> and passing that into React Flow's `colorMode` explicitly, so the canvas
> now follows the *app's* theme choice, not the raw OS setting underneath it.

Scope: this pass covered every page reachable in Phase 1 (auth, app shell,
assistants list, wizard, build page canvas/panels/config, chat) since they
all route through the same primitives and tokens. Phase 2+ pages will
inherit this automatically — no new design work needed per page, only new
tokens if a genuinely new semantic (e.g., a RAG citation highlight) shows up.

## 4. Phase 2 — RAG pipeline  *(complete)*

**A note before diving in: real API keys aren't wired up yet.** `.env` has
both `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` empty, so every chat turn so
far — including the whole Phase 1 manual walkthrough — ran on the offline
`FakeDriver`, never the real Claude model. That's fine for building the
pipeline's plumbing (which is what Phase 2 mostly is), but retrieval
*quality* can't be meaningfully judged until there's a real embedder behind
it (task 2.4) and a real model to read the results (any time).

### 4.1 The knowledge-base schema + `VectorStore` interface  *(done — task 2.1)*

**The domain model — three tables, one pipeline stage each:**

```
data_sources → documents → chunks
   (what the      (what it        (what actually
   user added)    parses into)    gets retrieved)
```

A `DataSource` is user-facing intent — "I uploaded this PDF" / "index this
URL" / "here's some pasted text." Ingestion (task 2.5, not built yet) turns
one data source into one or more `Document`s (a PDF might be one document; a
sitemap crawl might produce many), then splits each document into `Chunk`s —
the actual unit retrieval operates on, each carrying its own embedding vector
and full-text search vector. This mirrors exactly what
`docs/IMPLEMENTATION_PLAN.md` §2.3 specifies — models in `app/models/rag.py`.

**The interesting engineering problem: making pgvector-specific columns work
on SQLite too.** This project's test suite runs on SQLite by default (fast,
no Docker) with Postgres reserved for things that genuinely need it. But
`chunks.embedding` is a `vector(1024)` and `chunks.tsv` is a `tsvector` —
neither type exists on SQLite, and there's no meaningful fallback for real
similarity search or full-text ranking there. Following the same pattern
`TZDateTime`/`JSONB` already established in `app/db/types.py`:

- **`TSV`** — just `Text().with_variant(TSVECTOR(), "postgresql")`, one line,
  same trick as `JSONB`.
- **`Embedding(dim)`** — a full `TypeDecorator`: `pgvector.sqlalchemy.Vector`
  on Postgres (real HNSW-indexed similarity search), a JSON-encoded float
  list in a `TEXT` column on SQLite (enough to round-trip a value for offline
  model tests — see `tests/test_rag_models.py` — but `<=>` distance queries
  only work against Postgres).

> **A real gotcha this surfaced**: wrapping `pgvector.sqlalchemy.Vector` in a
> generic `TypeDecorator` silently breaks `.cosine_distance()` and friends —
> `TypeDecorator` does **not** proxy the underlying type's comparator methods
> by default, so `Chunk.embedding.cosine_distance(...)` raised
> `AttributeError` the first time it was tried. Fixed by explicitly setting
> `comparator_factory = Vector.comparator_factory` on `Embedding` — one line,
> but easy to miss, and the failure mode (an `AttributeError` deep in a query
> builder) doesn't obviously point at "the TypeDecorator ate my comparator."

**`VectorStore` (`app/rag/vectorstore/`)** — the swap point named in the plan
(pgvector today, Qdrant documented as a possible future swap). `base.py`
defines it as a `Protocol` (`upsert`, `query`, `delete_by_document`) plus two
plain dataclasses (`ChunkRecord` in, `ScoredChunk` out) so nothing above this
layer needs to know about SQLAlchemy models. `pgvector.py`'s `PgVectorStore`
is the only implementation: `query` orders by `Chunk.embedding.cosine_distance(...)`
(the HNSW index makes this fast), converts distance to similarity
(`1 - distance`, so "higher is better" everywhere above this layer), and
optionally joins through `Document` to scope a search to specific data
sources. `Embedder` and `Reranker` (`app/rag/embedders/`, `app/rag/rerankers/`)
are interface-only for now — no concrete Voyage/local implementation until
task 2.4; `retrieve.py` (2.7) is what will actually call them.

**Migration** (`4d19bec6d86b`): creates the three tables, then — guarded by
`if op.get_bind().dialect.name == "postgresql"`, since this same migration
also has to run cleanly against SQLite for `test_migrations.py` — defensively
re-enables the `vector` extension (the real bootstrap is
`docker/postgres-init/01-extensions.sql`, but that only runs on an empty data
dir) and raw-SQL-creates the HNSW index (`USING hnsw (embedding
vector_cosine_ops)`) and the GIN index on `tsv`. Verified directly against
the live container:

```
Indexes:
    "ix_chunks_embedding_hnsw" hnsw (embedding vector_cosine_ops)
    "ix_chunks_tsv_gin" gin (tsv)
```

**Testing this properly needed two tiers, not one.** `tests/test_rag_models.py`
runs on SQLite (fast, no Docker) and proves the models/round-trip work at
all. `tests/test_rag_vectorstore.py` is marked `@pytest.mark.integration`
and runs against the real dev Postgres — this is the *first* use of that
marker in the project (it existed in `pyproject.toml` since Phase 0 but
nothing had needed it yet). Each test opens its own transaction and rolls
back at the end, so running it never leaves rows in the shared dev database.
It proves the parts SQLite genuinely can't: cosine-similarity ranking
actually orders results correctly, `query` correctly scopes to one
assistant's chunks, and `ondelete="CASCADE"` really cascades
data\_source → document → chunk (SQLite doesn't enforce FK cascade without a
`PRAGMA` this app doesn't set, so that assertion was moved out of the SQLite
test entirely rather than asserting something SQLite can't actually promise).

> **Also fixed**: `scripts/check.ps1` ran plain `pytest apps/api -q`, which
> would have silently attempted the new integration tests on every local
> `check.ps1` run — fine right now since Docker Postgres happens to be up,
> but a hard failure for a future contributor without it running. CI
> (`.github/workflows/ci.yml`) already correctly deselected them
> (`pytest -q -m "not integration"`) since Phase 0; `check.ps1` just hadn't
> caught up. Now it runs both, as two explicit steps, so a missing Docker
> Postgres only fails the second (clearly-labeled) step, not the whole
> check silently mixing the two.

Verified: `ruff`/`mypy` clean, **70 passed + 4 deselected** on the default
run, **4 passed** on `-m integration` against the live container, web
typecheck/lint unaffected (backend-only task).

### 4.2 Data source CRUD + MinIO upload  *(done — task 2.2)*

**Scope, precisely**: this task creates `DataSource` rows and, for file
uploads, puts the raw bytes in MinIO. It does **not** parse, chunk, or embed
anything — a freshly created source just sits at `status="pending"` with
zero `Document`/`Chunk` rows. That's task 2.5 (the `ingest_data_source` Arq
task), deliberately not built yet. Three ways in, mirroring the three
`DataSourceType`s:

- **`POST /assistants/{id}/data-sources`** — JSON body, a Pydantic
  discriminated union on `type` (`CreateUrlSource | CreateTextSource`,
  `app/schemas/data_source.py`). `url` just records the URL — nothing fetches
  it yet. `text` stashes the pasted content in `DataSource.config["text"]`
  (the `config: jsonb` column earning its "per-type config" purpose) since
  there's no `Document` row to put it on until ingestion runs.
- **`POST /assistants/{id}/data-sources/upload`** — multipart file upload.
  Reads the bytes, computes a sha256 checksum, uploads to MinIO at
  `data-sources/{assistant_id}/{data_source_id}/{filename}`, then stores that
  key on the row.

**`app/storage/s3.py`** (new): a thin `boto3` wrapper. `boto3` is sync-only —
rather than pull in a separate async S3 client library, every call runs
through `asyncio.to_thread`. This is the right tradeoff here specifically
because uploads aren't hot-path (an occasional user action, not something
happening on every request), so the thread-hop cost is irrelevant; it
wouldn't be the right call for something like the per-turn chat path.

**Testing needed the same two-tier split as 2.1's `VectorStore`, for the same
reason: MinIO is external infra, just like Postgres.** URL/text creation and
list/get/delete never touch storage, so those run as ordinary tests through
the SQLite-backed `client` fixture. The one test that actually uploads a file
is marked `@pytest.mark.integration` and hits the real dev MinIO container.

> **Live-verified beyond pytest**, against a running server: uploaded a file
> over real HTTP, confirmed the object existed in MinIO at exactly the
> expected key (`mc ls` inside the container), deleted the data source, and
> confirmed the object was gone — not just the Postgres row. URL and text
> creation verified the same way, including that listing returns newest
> first.

> **A live-testing detour, not a code bug**: while doing this verification, a
> uvicorn process from *much* earlier in this session turned out to still be
> answering on port 8000 — `netstat` showed it LISTENING and it genuinely
> served stale (pre-2.2) API responses, but both `Stop-Process` and
> `taskkill` reported its PID as not existing. Windows networking oddity, not
> reproducible from application code. Verified instead by running a
> throwaway server on port 8001 for this one check.

Verified: `ruff`/`mypy` clean (added a `boto3` mypy override — no type stubs
upstream, same treatment as `opentelemetry` already got), **74 passed + 5
deselected** by default, **5 passed** on `-m integration`.

### 4.3 Parsers + the recursive chunker  *(done — task 2.3)*

**Scope, precisely**: pure functions only — `bytes in, structured text out`
for parsers; `ParsedDocument in, list[ChunkSpan] out` for the chunker.
Nothing here touches the database, MinIO, or an embedder. The Arq task that
wires "fetch a pending `DataSource` → parse → chunk → embed → write `Chunk`
rows" is task 2.5, still not built — these are the two building blocks it
will call.

**Five parsers (`app/rag/parsers/`), one shared output shape.** Every parser
returns a `ParsedDocument`: the full normalized text as one string, plus
`headings` (heading text + the character offset where it starts) and `pages`
(page number + character offset) when the format actually has that
structure. The honesty here matters for citations later: PDF gets real page
markers (`pypdf` extracts text per page, so offsets are exact) but no
heading detection (PDF headings are a font-size convention, not stored
structure — reliably inferring them is its own project, not attempted).
DOCX gets real headings (from paragraph style names — Word actually stores
"Heading 1".."Heading 6") but no page markers (DOCX doesn't store
pagination; it's computed at print time). HTML and Markdown get headings,
no pages. Plain text gets neither. `dispatch.py`'s `parse_by_mime` picks the
parser by MIME type, falling back to file extension when a browser-supplied
content type is `application/octet-stream` or otherwise useless.

**The chunker (`app/rag/chunking.py`) is a from-scratch reimplementation of
the "recursive splitter" pattern** (the same idea LangChain's
`RecursiveCharacterTextSplitter` popularized) — about 60 lines, not a new
dependency. "Recursive" means: try splitting on paragraph breaks
(`\n\n`) first; anything still too big gets split again on line breaks, then
sentence breaks, then words, then — for something with literally no
separators, like a long URL — hard character windows. `overlap` (e.g. 0.15)
pulls each chunk's start back into the *previous* chunk by that fraction of
the token budget, so consecutive chunks share trailing/leading context
instead of cutting mid-thought at a hard boundary. Every emitted `ChunkSpan`
carries its `breadcrumb` (the heading trail active at its start — e.g.
`["Chapter 2", "2.1 Setup"]`, built by walking `headings` with a small level-
aware stack) and `page` (the last page marker at or before its start),
so a chunk always knows exactly where in the source document it came from.

**Token counting is the same `len(text) // 4` approximation `FakeDriver`
already uses** for cost estimates (`app/agent/driver.py`) — good enough for
*sizing* chunks to roughly the right budget; swapping in a real tokenizer
later is a one-function change, not a rewrite.

**Testing needed real files, not mocks** — parsing logic is exactly the kind
of code that looks right and silently corrupts offsets in practice.
`tests/rag_fixtures.py` builds a genuinely valid minimal PDF by hand (correct
xref table and all — pypdf has zero tolerance for a missing one) and a real
DOCX via `python-docx`'s own writer, so `test_rag_parsers.py` runs the actual
parser code against actual file bytes, not synthetic dicts standing in for
them. `test_rag_chunking.py` checks the trickier chunker properties
directly: a single oversized paragraph with no breaks at all still gets
split (forces the recursion), overlap actually pulls chunk starts backward,
and — the one that would be easy to get subtly wrong — breadcrumb/page
values landing on the *correct* chunk when a heading falls in the middle of
a long document.

Verified: `ruff`/`mypy` clean, **89 passed + 5 deselected** by default, **5
passed** on `-m integration` (unchanged from 2.2 — this task added no new
integration-tier tests, since nothing here touches Postgres/MinIO). No live
HTTP verification this time — there's no endpoint or UI calling this code
yet (that's 2.5); the real-file parser tests are the honest equivalent of
"did this actually work," the same role the browser walkthroughs played for
UI-facing tasks.

### 4.4 Real embeddings: Voyage + a genuinely offline local fallback  *(done — task 2.4)*

**A detour before the code: getting real API keys without risking real
spend.** The user added real `ANTHROPIC_API_KEY` / `VOYAGE_API_KEY` values to
`.env` at this point — the first time either has existed in this project.
Rather than let that silently flip the app over to paid calls, `.env` now
pins two settings explicitly:

- **`AGENT_DRIVER=fake`** — `agent_driver`'s `"auto"` mode (the prior
  default) switches to the real `ClaudeSDKDriver` the moment a key is
  present. Pinning it to `"fake"` keeps chat on the free offline driver from
  1.4/1.5 until someone deliberately flips it for a one-off real check.
- **`RAG_OFFLINE=1`** — same idea for embeddings, and this task is what
  makes that flag actually do something (see below).

Neither setting is new — both already existed in `config.py` — this task
just started actually using `rag_offline`, and `.env`/`.env.example` now
document the "pin these to the free path by default" convention explicitly.

**Two implementations per interface, chosen by the same auto-detection
shape `get_driver()` already established:**

- **`VoyageEmbedder` / `VoyageReranker`** (`app/rag/embedders/voyage.py`,
  `app/rag/rerankers/voyage.py`) — plain `httpx` calls to Voyage's REST API.
  No `voyageai` SDK dependency; the API is simple enough (`POST
  /v1/embeddings`, `POST /v1/rerank`, one bearer header) that a dedicated
  client wasn't worth adding.
- **`LocalBgeEmbedder` / `LocalCrossEncoderReranker`** (`embedders/local_bge.py`,
  `rerankers/local.py`) — real neural models via `sentence-transformers`:
  `BAAI/bge-m3` for embeddings, `BAAI/bge-reranker-v2-m3` for reranking. This
  is a genuinely different kind of "offline fallback" than `FakeDriver` —
  `FakeDriver` stands in for chat during *testing* (a real answer isn't the
  point); the local embedder is meant to be **actually usable for real RAG,
  permanently, for free** — not just a test double. That's why it's a real
  ~2GB neural model, not a hash trick: retrieval quality is the whole
  product here, and a fake embedding would make the local path pointless
  to actually use.
- **`get_embedder()` / `get_reranker()`** (`embedders/__init__.py`,
  `rerankers/__init__.py`) mirror `get_driver()`'s logic exactly: offline
  flag *or* a missing key both fall back to local — a Voyage key alone is
  never enough to silently start spending, and a missing key never breaks
  the feature.

**A convenient coincidence, not a coincidence**: `bge-m3`'s native output is
1024-dimensional — exactly `EMBEDDING_DIM`, already fixed for
`voyage-3-large` back in 2.1. Swapping embedders never needs a
per-embedder dimension migration.

**Testing needed three tiers this time.** Voyage's HTTP calls are mocked via
`httpx.MockTransport` (monkeypatching `httpx.AsyncClient` itself, since the
embedder builds its own client internally — no network, no key needed,
fast). `get_embedder`/`get_reranker` dispatch logic is tested directly
against `monkeypatch`ed settings. And — the one that actually matters most —
`tests/test_rag_local_models.py` (`-m integration`) loads the **real**
`bge-m3` and `bge-reranker-v2-m3` models and checks real properties: a
query embeds closer (cosine similarity) to a genuinely relevant sentence
than an unrelated one, and the reranker actually puts the relevant candidate
first. Not "did it return *a* vector" — did the model actually understand
anything.

> **Live-verified for real**, not mocked: ran the integration suite end to
> end, which downloaded both models (~2GB, one-time, cached by
> `sentence-transformers` under the user's home directory for every future
> run) and proved retrieval quality, not just plumbing —
> `test_similar_texts_score_higher_than_unrelated_ones` and
> `test_reranker_orders_the_relevant_candidate_first` both passed on the
> first real run. Zero Voyage or Anthropic API calls were made anywhere in
> this verification — confirmed by construction, since `AGENT_DRIVER=fake`
> and `RAG_OFFLINE=1` were never touched.

Verified: `ruff`/`mypy` clean, **104 passed** total (96 unit + 8
integration — up from 89+5 in 2.3). Added `sentence-transformers` (CPU-only
torch, installed via `--extra-index-url
https://download.pytorch.org/whl/cpu` to avoid the much larger default CUDA
wheel).

### 4.5 `ingest_data_source` — everything finally wired together  *(done — task 2.5)*

**This is the task where 2.1–2.4 stop being separate pieces and become an
actual working pipeline.** `app/rag/ingest.py`'s `ingest_data_source(session,
data_source_id)` is a plain async function — not an Arq job itself, so it's
testable by just calling it — that does exactly what §5.1 of the plan
describes:

```
pending DataSource → fetch → parse → chunk → embed → write Chunk rows → ready
```

**Fetch, by type**: `file` sources read their bytes back from MinIO (new
`get_object()` in `app/storage/s3.py` — 2.2 only had `put`/`delete`, since
nothing needed to read a file back until now). `text` sources read the
pasted content straight off `DataSource.config["text"]` (where 2.2 stashed
it, with exactly this moment in mind). `url` sources get a genuinely new
capability: `app/rag/fetch.py`'s `fetch_url_text()` does a live HTTP GET and
runs the result through `trafilatura` — deliberately **not** the
`parsers/html.py` from 2.3, which parses HTML already in hand. A live page
has navigation, ads, and footers around the real content; trafilatura
specializes in stripping exactly that out, which a generic block-tag walker
isn't built to do.

**Reindexing is "delete then recreate," not deduplication logic.** The plan
describes chunk-id hashing for idempotency; this implementation gets the
same practical guarantee more simply: before writing a fresh `Document`,
`ingest_data_source` deletes any `Document` rows this `DataSource` already
produced (their `Chunk`s cascade-delete via the FK). Re-running ingestion —
whether the automatic first run or an explicit reindex — never leaves stale
or duplicate chunks. Verified directly: reindexing a source live left
`document_count = 1, chunk_count = 1`, not 2.

**The queue, minimally.** `app/queue.py` (`enqueue_ingest`) and
`app/worker.py` (`WorkerSettings`, `ingest_data_source_job`) are the whole
Arq integration — a job is just `ingest_data_source` wrapped in "open my own
DB session," the same shape as the SSE endpoint's own session handling from
1.5. `enqueue_ingest` is wrapped in try/except and swallows any failure,
logging a warning instead — the same "disposable infra" contract
`session_store.py` established for Redis in 1.7: if the queue is down, a
data source just stays `pending` until someone retries, rather than the
create/upload request itself failing. `create_url`/`create_text`/
`create_upload`/`reindex` in `app/services/data_sources.py` all call it
after their own commit.

**New pieces to actually run a worker**: `scripts/dev-worker.ps1` (mirrors
`dev-api.ps1` — `arq app.worker.WorkerSettings`), a `worker` service in
`docker-compose.yml` (same image as `api`, different command, migrations
skipped since `api` already runs them), and `docker/api.Dockerfile`'s
`pip install` gained the same `--extra-index-url` CPU-torch flag the local
install needed in 2.4 — otherwise the Docker image would silently pull the
large default CUDA wheel.

**Scope note**: "status/progress events" from the plan's task description is
scoped down to status persistence only (`pending → processing → ready` /
`error`, `indexed_at`, `error` message) — no live progress-streaming
mechanism. There's no UI polling or displaying this yet (that's 2.12); live
progress events are worth building when there's something to receive them.

> **Live-verified through the real path, not just pytest**: started the API
> and a real Arq worker as two separate processes, then over genuine HTTP —
> created a text source ("Refund Policy") and watched it transition
> `pending → processing → ready` by polling, then confirmed directly in
> Postgres: one chunk, `vector_dims(embedding) = 1024`, content matching what
> was submitted. Created a **url** source pointed at `https://example.com`
> and confirmed trafilatura correctly extracted the real page body ("This
> domain is for use in documentation examples...") — not raw HTML, not
> boilerplate. Called the reindex endpoint on the text source and confirmed
> `indexed_at` advanced and the chunk/document count stayed at exactly 1,
> not 2. The worker log also surfaced something real: several jobs enqueued
> by earlier pytest runs (Redis isn't mocked in tests, so `enqueue_ingest`
> genuinely queues them) were sitting in the real queue; the worker drained
> them cleanly as no-ops, since `ingest_data_source` already handles a
> missing `DataSource` row gracefully (`if source is None: return`) —
> confirming that safety path works, not just in theory.

Verified: `ruff`/`mypy` clean, **111 passed** total (99 unit + 12
integration — up from 96+8 in 2.4). Added `arq` and `trafilatura`
dependencies.

**Phase 2 status**: 2.1–2.5 done — the knowledge base pipeline works end to
end (add a source → it gets indexed automatically → real chunks with real
embeddings land in Postgres). What's not built yet: retrieval itself
(`retrieve.py`, hybrid search + RRF + rerank, task 2.7), the `kb_search`
tool that would let an agent actually use any of this (2.8), citations
(2.9), and the canvas/UI pieces (2.12, 2.13).

### 4.6 Hybrid retrieval: dense + sparse + RRF + rerank  *(done — task 2.7)*

**This is the task where the knowledge base becomes queryable — the first
thing that reads it, not just writes to it.** `app/rag/retrieve.py`'s
`retrieve()` follows the plan's §5.2 algorithm exactly:

```
embed query → dense search (pgvector cosine) ─┐
                                                ├→ RRF fuse → rerank → threshold → attach source metadata
           → sparse search (Postgres FTS)  ────┘
```

(Query expansion — the plan's optional step 1, off by default — isn't
built; `RagRetrieval.max_queries` stays unused for now, a deliberate v1
scope cut matching the plan's own "optional, default off" framing.)

**A real gap 2.1/2.5 left open: `chunks.tsv` was never populated.** The
column existed since 2.1 and every chunk got a real `embedding`, but nothing
ever wrote to `tsv` — sparse search had no data to search. Fixed in
`PgVectorStore.upsert` (not in ingestion — the vectorstore is the only
place chunks get written, so that's where `tsv` population belongs too):
right after inserting, one `UPDATE ... SET tsv = to_tsvector('english',
concat(context_prefix, ' ', content))` for the new rows. Postgres's
`concat()` (unlike `||`) treats `NULL` as empty string, so a chunk without a
`context_prefix` (every chunk today — that's 2.6, not built) doesn't null
out its whole search vector.

**`query_sparse`** (new on `VectorStore`) mirrors `query`'s shape:
`ts_rank_cd(tsv, websearch_to_tsquery(...))`, filtered to actual `@@`
matches (unlike dense search, sparse can genuinely return zero results —
there's no "closest anyway" fallback for full-text).

**RRF fusion is pure Python, no DB** (`rrf_fuse` in `retrieve.py`) —
deliberately factored out so it has its own fast unit tests
(`test_rag_rrf.py`), matching what the plan's own testing section names
explicitly: *"Unit: ... RRF fusion ..."* (§11.1). `score(chunk) = Σ 1/(rrf_k
+ rank_i(chunk))` over every ranked list it appears in — a chunk both
searches agree on outranks one only one method found, which is the entire
point of doing hybrid search instead of picking one.

**Reranking and thresholding happen in that order**, per the plan: the
RRF-fused candidates go to `Reranker.rerank()` in full, then get sliced to
`rerank_top_n`, and *only then* does `min_score` drop weak results — the
threshold applies to the reranker's score (semantically meaningful, roughly
0-1) rather than the RRF score (a fusion artifact with no natural scale).

**Source metadata is attached last, only for survivors** — one join query
against `Document`/`DataSource` for just the handful of chunks (typically
≤`rerank_top_n`, e.g. 8) that made it through reranking and the threshold,
not for the whole `top_k_dense + top_k_sparse` candidate pool. `RetrievedChunk`
carries `title`, `uri`, `page`, `breadcrumb` — everything task 2.9's
citations will need, already sitting on the object.

> **Live-verified for real**: the integration suite seeds a tiny 3-chunk
> knowledge base (refund policy / office hours / onboarding) using the real
> local embedder, then asks `retrieve()` "how long do refunds take?" —
> the refund chunk comes back first, with `title == "Handbook"` and a real
> `data_source_id` attached. A second test sets `min_score=0.999` (an
> almost-impossible bar) and confirms nothing survives it. A third confirms
> querying a *different* assistant's id returns nothing, even though the
> content exists — the tenant scoping in `query`/`query_sparse` actually
> holds under a real hybrid search, not just in a unit test with mocked
> data.

Verified: `ruff`/`mypy` clean, **121 passed** total (104 unit + 17
integration — up from 99+12 in 2.5). No live HTTP check this task either —
same as 2.3's parsers, nothing calls `retrieve()` yet (that's 2.8's
`kb_search` tool); the integration tests against real infra are the honest
equivalent.

### 4.7 `kb_search` / `kb_list_sources` — an agent can finally use the KB  *(done — task 2.8)*

**This is the task where the knowledge base becomes something an agent can
actually reach for, not just something that exists.** Every prior RAG task
(2.1–2.7) built plumbing nothing in the chat path called. `kb_search` and
`kb_list_sources` are the two tools that close that gap.

**Why these two tools couldn't live in `app/agent/caps.py` the way
`calculator`/`datetime` do.** Those two are static and stateless — built
once, looked up by name from `ALL_CAPS`. `kb_search` needs to know *which
assistant's* knowledge base to search and *what retrieval settings* to use
— that's per-turn context, not something a module-level dict can hold. New
`app/agent/caps_rag.py` has factory functions instead:
`build_kb_search_tool(assistant_id, rag_config)` and
`build_kb_list_sources_tool(assistant_id)`, each returning a
`CapabilityTool` whose handler *closes over* that context. `options.py`'s
`_enabled_caps` calls these factories fresh every turn instead of doing a
static lookup — the same function, now also taking an `assistant_id`
parameter threaded in from `chat.py` → `Turn` → `build_runtime_spec`.

**Each handler opens its own DB session** via `get_sessionmaker()` — same
shape as `app/worker.py`'s job function: a tool call happening mid-turn
isn't the outer chat request's session's business. `kb_search`'s handler
calls `retrieve()` (2.7) and formats the results as numbered, cited text:

```
[1] Refund Policy (source_id=...)
Refunds are processed within 5 business days of the original request.
```

— matching exactly what the new system-prompt instructions
(`compose_system_prompt`) tell the model to expect: *"Its results are
numbered [1], [2], ... — when you use information from a result, cite it
inline with that marker."* This is the "citation instructions" half of
2.8's task description; full citation *tracking* (mapping `[n]` markers
back to sources in the persisted message, SSE `citation` events) is task
2.9, not built here.

**A real gap this surfaced while testing, not while building**: the
handlers' `get_sessionmaker()` call resolves to whatever `DATABASE_URL` the
*app* is configured with — in the test suite, that's forced to SQLite. My
first integration test seeded real chunks into real Postgres through a
*separate* connection, then called the handler expecting it to find them —
it couldn't, because the handler's own session was still pointed at the
(empty) SQLite test DB. Two of the four assertions failed outright; a third
silently returned "no results" instead of erroring, which is the more
dangerous failure shape. Fixed by making the test file itself repoint the
app's `get_sessionmaker()`/`get_engine()` cache at the same Postgres for its
own duration (`app/db/session.py`'s `@lru_cache`, cleared and rebuilt with
`settings.database_url` monkeypatched) — the production code was correct
throughout; the test's assumption about which database "the app" meant was
wrong.

**`FakeDriver` gained a `kb_search` branch**, mirroring its existing
calculator branch: a prompt containing "search" plus a `kb_search` tool in
`spec.caps_tools` triggers a real tool-call → tool-result → reply sequence,
looked up from the per-turn `caps_tools` list rather than the static
`ALL_CAPS` registry (since kb_search isn't in it). This is what makes the
live HTTP verification below possible without spending any API credits.

> **Live-verified through the real path — reusing real data from 2.5's
> verification**, not fresh fixtures: took the "Ingest Live Test" assistant
> (its "Refund Policy" data source from task 2.5's live check was still
> sitting in Postgres), flipped `rag.enabled = true` on its draft config via
> the real API, then sent a real chat message — *"please search the
> knowledge base: how long do refunds take?"* — over SSE. The stream came
> back exactly as designed: a `tool_call` for `mcp__caps__kb_search`, a
> `tool_result` containing `"[1] Refund Policy (source_id=f11efa49-...)\nRefunds
> are processed within 5 business days..."`, and a final token stream
> quoting that same cited text. Every layer — HTTP, chat turn, FakeDriver,
> the real `retrieve()`, real Postgres, real embeddings — fired for real, in
> the order the design says it should.

Verified: `ruff`/`mypy` clean, **130 passed** total (109 unit + 21
integration — up from 104+17 in 2.7).

**Phase 2 status**: 2.1–2.8 done. An assistant with a knowledge base can now
genuinely answer questions from it, with citations, inside a real
conversation. Still missing: citation *tracking* into persisted messages and
SSE `citation` events (2.9), the retrieval subagent (2.10, opt-in/P2), eval
metrics (2.11, P1), and the two UI-facing tasks — data-sources uploader
(2.12) and canvas KB/data-source node types (2.13), both P0 but backend-only
until someone builds the frontend for them.

### 4.8 Citations: `[n]` markers back to their sources  *(done — task 2.9)*

**This is the task where retrieval stops being something you have to trust.**
2.8 taught the model to *write* `[1]` markers; nothing anywhere read them
back. An answer citing `[2]` was a claim with no way to check it. 2.9 maps
those markers to the chunks they came from, streams them as `citation` SSE
events, persists them on the message, and renders a sources panel with deep
links — the plan's §5.3, end to end.

**The problem that shapes the whole design: `[n]` is ambiguous.**
`kb_search` numbers its own results `[1]..[8]` on *every call*. So:

- An agent that searches twice in one answer emits **two `[1]`s**, and at
  finalize there is no way to tell which chunk the model meant.
- Worse, the SDK session is **resumed** across turns (`chat.run_message` →
  `options.resume`, from task 1.7). Turn 2's context still contains turn 1's
  numbered results. A registry that restarts each turn would hand `[1]` to a
  *different* chunk, so the model's perfectly reasonable reuse of `[1]`
  resolves to the wrong document — a confident citation pointing somewhere
  else, with no error anywhere. That is the worst failure this feature has,
  and far worse than showing no citation at all.

So `app/agent/citations.py`'s **`CitationRegistry` is scoped to the
conversation, not the turn** — the same lifetime as the model's context. It
hands out numbers that are never reused, gives a re-retrieved chunk the
number it already had, and persists its state (`{chunk_id: marker}` plus the
high-water mark) on a new `conversations.citation_state` JSONB column
(migration `7c2f4a91be30`). It stores the whole *registered* map, not just
what got cited — `resolve()` drops uncited registrations, so seeding from the
persisted citations alone would under-count and re-issue numbers that are
still live in the model's context.

**Reading markers out of prose is not just a regex.** The naive
`\[(\d+)\]` has a false positive that actively fabricates attribution: this
assistant can answer coding questions, and `rows = data[1]` is not a
citation. Two defences, both in `citations.py`:

1. Fenced blocks and inline code are **blanked with same-length spaces**
   before scanning — same length so every char offset stays valid.
2. A run of adjacent groups must not start after a word character or a
   closing bracket. The guard applies to the *run*, not to each group,
   because checking groups independently cannot tell `[1][2]` (two citations,
   which models write constantly) from `a[0][1]` (chained indexing) — in both
   the second `[` follows a `]`. Anchoring at the run's start separates them.

The lookbehind does drop a citation written glued to a word (`policy[1]`),
which is lexically identical to `items[1]`. That asymmetry is deliberate: a
missed citation renders as inert text, a false one invents a source. The
system prompt asks for a space before the bracket to cover the gap.

**The UI never re-derives markers.** `resolve()` records each marker
occurrence's `[start, end)` offsets in the final content, and the sources
panel splits the string by those. Re-deriving them in TypeScript would mean
reimplementing the code-masking above and disagreeing with the backend the
first time either side drifted.

**Where resolution happens is a correctness decision, not a tidiness one.**
It runs in `chat.run_message` *after* the commit, not inside `Turn.stream()`:

- `RetrievedChunk.data_source_id` is nullable (the chunk→document→source join
  can miss). Resolving before the commit means a bad value destroys an answer
  the user has already watched stream in — message, run row, spend and
  `sdk_session_id` all roll back. It is wrapped in try/except for the same
  reason.
- The SSE route breaks out of the generator on client disconnect, so every
  pre-commit `yield` is another point where a disconnect skips persistence.
  Emitting citations post-commit adds none.
- Ids are `str`, stringified at the registry boundary. `message.blocks` is
  JSONB written with the stdlib `json.dumps`, which cannot serialise a
  `uuid.UUID` — it would raise at `flush()`, again *after* the answer had
  streamed.

**`message.blocks` becomes typed.** Entries now carry `{"type": "tool_call"}`
or `{"type": "citation"}`. The important detail is on the read side: rows
written before 2.9 have **no `type` key at all**, so the frontend filters on
`type !== "citation"` for tool calls. A positive `type === "tool_call"` filter
would silently erase tool cards from every existing conversation.

**Deep links, honestly per source type** — a web source gets its original URL
plus a `#:~:text=` fragment; a file gets a short-lived presigned URL plus
`#page=N`; pasted text gets nothing and says so, because a dead "Open" link
is worse than none. The presigned URL exists because a link click carries no
`Authorization` header (tokens live in localStorage), so an authenticated
route cannot serve a file to a new tab. `S3_PUBLIC_ENDPOINT` is new: inside
Docker the API reaches MinIO at `http://minio:9000`, a hostname no browser
can resolve, and the host is part of the signature so it cannot be rewritten
afterwards — it has to be signed against the public endpoint from the start.

> **Three bugs in already-shipped code that this task exposed**, all found by
> reviewing the design against the real code *before* building it:
>
> 1. **`chunk_document` reported the wrong page and heading for every chunk
>    after the first.** It derived `page=_page_at(doc.pages, start)` from the
>    *overlap-adjusted* start, which sits in the **previous** page. Verified
>    on a real 6-page document: chunk 1 spans 1442–3844 with its body entirely
>    on page 2, tagged page 1. Every `#page=N` deep link would have opened the
>    wrong page, and `breadcrumb` had the identical defect. Locators now come
>    from `span_start` — where the chunk's own content begins. The existing
>    guard test passed `overlap=0.0`, where the two offsets coincide, so it
>    stayed green the whole time; the new tests run at the production 0.15 and
>    fail against the old code.
> 2. **Chunks routinely began mid-word** — `start` is pure arithmetic, so a
>    chunk's text started `"ge 1 content"` instead of `"Page 1 content"`. A
>    browser text fragment only matches on word boundaries, so the URL deep
>    link would have silently highlighted nothing. Starts now snap forward to
>    the next boundary, costing at most one word of overlap.
> 3. **`register` never committed.** It only `flush()`ed, leaving the commit
>    to `get_session`'s teardown — which runs *after* the response is sent. A
>    client using the token it was just handed could beat the row into
>    existence and get `401 User not found`. Phase 1 fixed exactly this race
>    for assistants and conversations; auth was missed. Found the hard way:
>    it broke the live verification run for this task, intermittently.
>
> Also fixed in passing: `boto3` presigns with the legacy SigV2 querystring
> signer by default for a custom endpoint. MinIO accepts it, so dev would
> never have noticed, but AWS S3 has retired SigV2 outside a few legacy
> regions — the default would break the moment this pointed at real S3.

**Two consequences worth stating plainly rather than hiding.** Chunks indexed
before 2.9 have no char offsets *and* a wrong page number, so **a reindex is
mandatory, not optional**, for citations to point anywhere correct. And a
chunk long enough to straddle a page break now records `page_end` too, so the
panel renders "pp. 3–5" instead of implying a single page it does not have.

**Testing had to dodge a trap this project has hit before.** The headline
"citation events over SSE" test cannot run against the real `kb_search` in
the unit tier: `DATABASE_URL` is SQLite there, the handler opens its own
session, and pgvector's `embedding <=> ?` does not parse. The resulting error
is caught by the SSE route's blanket handler and returned as an
`internal_error` frame **on a 200 response** — so a test asserting "the
request succeeded" would pass with the entire feature unexercised. The unit
tier monkeypatches `app.agent.caps_rag.retrieve` instead, and every test
asserts a terminal `done` and the absence of `error`, never just a 200.
`FakeDriver` also gained a two-call branch ("search twice"), because the
global-numbering property is the whole point of the registry and one tool
call cannot exercise it.

> **Live-verified end to end, not just by pytest**: real API + real Arq
> worker + real Postgres/MinIO. Uploaded a genuine 3-page PDF (built with the
> project's own fixture builder), watched it index, then over real SSE — turn
> 1 returned three `citation` events for page-2 refund chunks (the refund text
> really is on page 2), and turn 2 came back with markers **4 and 5**, not a
> restarted 1 and 2. `conversations.citation_state` in Postgres showed five
> markers and `next: 6`. Every recorded span was checked by slicing the
> *persisted* message content at those offsets — each one sliced out exactly
> `[1]`, `[2]`, … The presigned link fetched `200 application/pdf` from the
> browser with no auth header. In the browser: the sources panel rendered for
> both a live streaming turn and a reloaded one, a second search correctly
> **reused** markers 1–3 for the same chunks, and a message hand-edited in
> Postgres to the pre-2.9 untyped-blocks shape still rendered its tool card —
> the regression that would otherwise have blanked every existing
> conversation.

One honest limitation of offline verification: `FakeDriver` passes the whole
prompt to `kb_search` as the query, so a prompt like *"please search the
knowledge base: how long do refunds take?"* scores badly against the
reranker (0.01 — the instruction prefix drowns the question) and gets
filtered by `min_score`. A real model would extract a clean query. It is a
property of the test double, not of retrieval, but it is worth knowing before
concluding "RAG returned nothing" during a manual check.

Verified: `ruff`/`mypy` clean, **168 passed** total (146 unit + 22
integration — up from 109+21 in 2.8), web typecheck + lint clean.

**Phase 2 status**: 2.1–2.5 and 2.7–2.9 done. A knowledge-base assistant now
answers from its sources *and* shows its working: every `[n]` in the answer
is a clickable chip tied to a panel entry with a snippet, a page or char
range, and a link that opens the actual source. Still missing: the retrieval
subagent (2.10, opt-in/P2), eval metrics (2.11, P1), contextual-retrieval
prefix (2.6, P1/optional), and the two remaining UI tasks — data-sources
uploader (2.12) and canvas KB/data-source node types (2.13).

### 4.9 The data-sources UI  *(done — task 2.12)*

**Everything 2.2–2.9 built had no front door.** Adding a knowledge base meant
POSTing JSON by hand; watching it index meant polling the API in a terminal;
a failed ingestion was invisible unless you read the `error` column in
Postgres. This task is the screen that makes the pipeline usable — the plan's
2.12: *"uploader, status chips, counts, reindex/delete"*.

**It's a tab on the build page, not a separate route.** Canvas / Panels /
**Sources** / Config JSON. Sources belong to one assistant and nothing else,
and 2.13 is about to put `knowledge_base` and `data_source` nodes on the
canvas right next door — keeping them one click apart beats a separate page
that has to re-establish which assistant you're looking at.

**The one backend change: counts.** `DataSourceSummary` knew a source's
status but not whether indexing actually *produced* anything, and "ready with
zero chunks" is a real state — a PDF that parsed to nothing, an empty text
file. New `counts_for()` returns `{data_source_id: (documents, chunks)}` for
a whole assistant in **one grouped query** rather than two per row; twenty
files would otherwise mean forty extra queries just to render the list. Two
SQL details that are easy to get wrong and silently plausible:

- `count(distinct Document.id)` — the outer join to chunks multiplies
  document rows, so a plain `count(Document.id)` reports 6 documents for one
  document with 6 chunks.
- `count(Chunk.id)`, not `count(*)` — `count(*)` counts the outer-join's NULL
  row, so a document with no chunks reports 1 chunk instead of 0, which is
  exactly the case the UI needs to warn about.

Both are pinned by tests that were checked to fail when the query is mutated
back to the naive form.

**Polling, but only while something is in flight.** Ingestion is an Arq job
with no push channel to the browser (2.5 deliberately scoped live progress
events out — there was no UI to receive them, and now there is one, polling
is enough). The list refreshes every 2s *while* any source is `pending` or
`processing`, and the interval is torn down the moment everything settles —
an idle Sources tab makes no requests at all. The "indexing…" hint next to
the header is the same condition, so the UI never looks busy while it's idle.

**Multipart needs its own path.** `lib/api.ts`'s `request()` pins
`Content-Type: application/json`; for a `FormData` body the browser has to
set that header itself so it can include the multipart boundary. `uploadFile`
is therefore a separate function that reuses the same auth/org headers and
the same `ApiError` envelope, rather than a flag on `request`. Uploads run
**sequentially**, not through `Promise.all` — each body can be tens of
megabytes and one failure shouldn't take the others down with it.

**Honest states rather than flattering ones**: the row shows the backend's
actual `error` string when ingestion failed (`[Errno 11001] getaddrinfo
failed` for an unreachable URL, verified live), and a `ready` source with
zero chunks gets an explicit *"Indexed but produced no chunks — nothing here
is searchable"* warning instead of a reassuring green chip over an empty
index. Delete confirms, and says what it actually does: the FK cascade takes
the documents and chunks with it, so the assistant genuinely forgets the
source.

> **Browser-verified end to end** against a real API, a real Arq worker and
> real Postgres/MinIO, on a freshly registered account: added a **text**
> source and watched the chip go `pending → processing → ready` purely from
> polling, ending at `1 doc · 1 chunk`; added a **URL** source
> (`https://example.com`) whose name correctly defaulted to the URL; uploaded
> a **file** by dispatching a real drag-and-drop onto the drop zone, and
> confirmed the object actually landed in MinIO at the expected key via
> `mc ls`. Clicked **Reindex** and watched the source return to `pending` and
> come back `ready` with its counts. Added a deliberately unresolvable URL
> and got a red `error` chip with the real backend message. **Deleted** a
> source and confirmed both the confirm text and that the row disappeared.
> Finally — the check that matters most — enabled `rag` and chatted: the text
> source added *through this UI* came back as a real `kb_search` hit, cited
> as `[1] Refund Policy · text · chars 0–182` in 2.9's sources panel. The
> whole pipeline, driven entirely from the browser.

> **A gap worth naming rather than papering over**: there is still no way to
> *turn the knowledge base on* from the UI. `rag.enabled` is compiled from a
> `knowledge_base` node wired to the agent (`app/graph/compile.py`), and the
> canvas node types for that are **task 2.13**. So after 2.12 you can add,
> watch, reindex and delete sources — but flipping `rag.enabled` still needs
> the API. The verification above enabled it that way deliberately, rather
> than adding a second place that writes `rag.enabled` and pre-empting 2.13.

Verified: `ruff`/`mypy` clean, **173 passed** total (151 unit + 22
integration — up from 146+22 in 2.9), web typecheck + lint clean.

**Phase 2 status**: 2.1–2.5 and 2.7–2.9 plus 2.12 done. The only remaining
P0 is 2.13 (canvas `knowledge_base` / `data_source` nodes), which is also
what closes the "enable RAG from the UI" gap above. Still optional/lower
priority: contextual-retrieval prefix (2.6, P1), retrieval subagent (2.10,
P2), eval metrics (2.11, P1).

### 4.10 Canvas knowledge-base and data-source nodes  *(done — task 2.13, Phase 2 complete)*

**This is the task that closes the loop Phase 2 has been building toward.**
Before it, an assistant's knowledge base could be *managed* (2.12) and
*used* (2.7–2.9), but not *turned on* — `rag.enabled` compiles from a
`knowledge_base` node wired to the agent, and nothing in the UI could create
one. Enabling RAG meant a hand-written API call. Now it's dragging a node.

**Most of this task turned out to be already built.** `app/graph/` has had
`KnowledgeBaseNode`/`DataSourceNode`, the `data_source → knowledge_base →
agent` edge rules, and the compiler mapping to `RagConfig` since task 1.12 —
the Phase-1 round-trip spike deliberately modelled the whole node vocabulary
up front. What was missing was entirely on the canvas side, and it was more
than "two node types": **the canvas had no way to add, connect or delete
anything**. `nodesConnectable={false}`, `deleteKeyCode={null}`, no palette,
no `onConnect`. You could move a node and edit its data; the shape of the
graph was fixed at creation.

**The edge allow-list is served, not copied.** The canvas has to know which
connections are legal to offer — and the obvious implementation, a copy of
the rules in TypeScript, fails silently in *both* directions when it drifts:
the canvas lets you draw an edge the API then rejects, or refuses one that
was actually fine. So `GET /meta/graph-schema` returns the validator's own
`ALLOWED_EDGES`, node types and singleton list, and React Flow's
`isValidConnection` is built from that response. The constants were renamed
from `_ALLOWED_EDGES` to `ALLOWED_EDGES` to make them importable, and the
endpoint's tests assert it *is* those constants rather than a copy that
happens to agree today. If no schema has loaded yet the canvas is
permissive — a legal connection silently refused is worse than one the API
rejects with a message.

**Adding a node pre-wires it.** A `knowledge_base` node that isn't connected
to the agent compiles to nothing, so dropping one unconnected means the first
thing the user sees is a warning about an orphan. The palette therefore adds
the node *and* the edge to the agent; a data source node likewise attaches to
the knowledge base if one exists. Node deletion removes every edge touching
the node in the same operation — a leftover edge pointing at a node that no
longer exists is a hard `unknown_edge_endpoint` validation error, so they
have to go together or the graph stops saving.

**The palette offers two node types, not twelve.** `database`, `tool`,
`mcp_server` and `subagent` all exist in the graph model, but they reference
things that don't exist yet — a connection id, an MCP server registration.
Offering them would let someone build a graph that can never validate. They
arrive with their own phases.

> **A real footgun this task created, and closed.** `compile_graph` takes
> `kb_nodes[0]`: a *second* knowledge base wired to the agent contributes
> nothing, so its carefully-tuned settings would be dead config the user
> believes is live. That was unreachable before — no UI could create one.
> Now that the canvas can, `validate_graph` raises
> `duplicate_knowledge_base` as an **error** (not a warning: publishing a
> graph whose settings are silently discarded is worse than being blocked),
> flagging the *extra* node rather than the one that takes effect. The
> palette also disables the button and says why.

**Being honest about `embedder` / `reranker`.** The KB drawer exposes them
because the plan asks for it, but `get_embedder()`/`get_reranker()` dispatch
purely on `RAG_OFFLINE` and whether a Voyage key is present — they never read
these fields. Rather than ship a dropdown that looks like it switches models
and doesn't, the field carries that caveat in its hint text. Same honesty for
chunking: those values apply to the *next* indexing run, so the hint points
at the Sources tab's Reindex button.

**A bug the browser found that tests wouldn't have.** The build page loaded
its data-source list once on mount. Add a source in the Sources tab, switch
to Canvas, and the palette still said *"None yet — add one in the Sources
tab."* Both tabs live in the same mounted component, so nothing re-fetched.
Fixed by refreshing the list whenever the canvas comes back into view. This
is exactly the class of defect that only shows up when you actually click
through the app in the order a user would.

> **Verified end to end in the browser, building RAG entirely from the UI for
> the first time**: created a fresh assistant, added a "Warranty Terms" text
> source in the Sources tab, then on the canvas added a knowledge base node
> and a data source node from the palette and let them auto-wire. The
> compiled config came back `rag.enabled: true` with `source_ids` scoped to
> exactly that source, graph `ds→kb→agent`, **zero errors and zero
> warnings** — no API call anywhere. Edited "Results kept after reranking"
> from 8 to 3 in the drawer and confirmed it propagated to both the node and
> the compiled config *with every sibling retrieval field intact* (the
> `nest()` helper exists because the node's sub-objects are `extra="forbid"`,
> so a partial patch is a 422), and the canvas node's subtitle updated to
> "3 results" live. Then chatted: `kb_search` hit the source and the answer
> cited `[1] Warranty Terms · text · chars 0–206` in 2.9's sources panel.
> Finally the inverse — removed the knowledge base node via the drawer and
> confirmed `rag.enabled` flipped to `false`, `source_ids` emptied, *both*
> touching edges disappeared with it, and the now-unconnected data source
> raised the `orphan_data_source` warning that surfaced as an amber badge in
> the header.

Verified: `ruff`/`mypy` clean, **180 passed** total (158 unit + 22
integration — up from 151+22 in 2.12), web typecheck + lint clean.

**Phase 2 is complete on its P0 scope**: 2.1–2.5, 2.7–2.9, 2.12 and 2.13. An
assistant's knowledge base can now be built, filled, wired, tuned, searched
and cited entirely from the browser. Deliberately left: contextual-retrieval
prefix (2.6, P1/optional), retrieval subagent (2.10, P2/opt-in) and retrieval
eval metrics (2.11, P1) — the last of which is the one worth coming back for,
since nothing yet measures whether retrieval is actually *good*.

### 4.11 The rest of Phase 2: contextual retrieval, the retrieval subagent, and eval metrics  *(done — tasks 2.6, 2.10, 2.11)*

The three items Phase 2 had deliberately deferred — the two P1s and the P2.
Taken together they close the phase: the pipeline can now be made *better*
(2.6), made *cheaper to run well* (2.10), and — the one that was actually
missing — **measured** (2.11).

#### 2.6 — Contextual retrieval

Chunking destroys context. A chunk reading *"This applies for thirty days
from purchase"* is nearly unretrievable: "this" and "purchase" of **what**?
Anthropic's contextual-retrieval technique fixes that by prefixing each chunk
with a sentence or two of LLM-written context before embedding it, and
`app/rag/contextualize.py` is that, per the plan's §5.1 step 4.

**The prefix is embedded, never stored as content.** `apply_prefix` feeds
`prefix + chunk` to the embedder (and `PgVectorStore.upsert` already concats
`context_prefix` into the `tsv`), while `Chunk.content` keeps the document's
own words. A citation has to quote what the source actually said, not a
model's description of where it sits.

**This is the first thing in the project that spends money on its own.**
Every prior cost was attached to someone pressing send; this fires on a file
upload, once per chunk. So `get_contextualizer()` takes **three independent
vetoes**, any one of which forces the free `NullContextualizer`:

* the assistant's own `rag.contextual_retrieval`,
* the environment's free-path pins — `RAG_OFFLINE=1` ("no paid RAG calls") or
  `AGENT_DRIVER=fake` ("no real model calls"),
* an actual key (and never from the test suite).

That reuses the exact shape `get_driver()` and `get_embedder()` already
established rather than inventing a fourth flag to remember. Failures are
isolated per chunk — a missing prefix costs a little retrieval quality, a
raised exception would cost the user the whole ingestion — and the spend
lands on the `usage_events` ledger, because contextualisation happens in the
worker and never passes through `chat.run_message`'s accounting.

> **A bug caught by the project's own conventions**: the module first used
> the dated id `claude-haiku-4-5-20251001`, which isn't in `PRICE_PER_MTOK` —
> so every contextualisation run would have silently costed **$0.00**.
> `app/agent/models.py` says "keep every model-id string in this module" for
> exactly this reason; the id now lives there as `CONTEXTUALIZE_MODEL` and
> prices correctly.

#### 2.10 — The retrieval subagent

Why delegate retrieval when the main agent already has `kb_search`? Because
searching well is iterative and noisy — two or three queries, skim,
reformulate, discard near-duplicates — and done inline, every failed query
and rejected passage stays in the main agent's context for the rest of the
conversation, on the expensive model. A subagent runs that loop in its **own**
context on a cheap model and returns only what it kept.

`app/agent/subagents.py` builds an SDK-agnostic `SubagentSpec` (so
`FakeDriver` can see what would have been configured) that
`build_claude_options` converts into the SDK's `AgentDefinition` map for
`options.agents`, alongside `forward_subagent_text=True` so a delegated
search isn't a silent pause in the stream.

Two details that are load-bearing rather than decorative:

* **"Do not answer the question"** in the subagent's prompt. If it answers,
  the main agent tends to relay that answer, and the citation markers never
  reach the final turn — they belong to the subagent's context.
* It is gated on `rag.enabled`, not just on the toggle. An enabled retrieval
  subagent with no knowledge base gets tools with nothing behind them and
  burns a turn discovering that. The graph validator independently warns
  about the same shape (`bare_subagent`), and the new **Subagents** panel says
  so in plain words — three places that agree instead of one that silently
  wins.

#### 2.11 — Retrieval eval metrics

`app/evals/` — the retrieval slice of the plan's §11.2 (Claude-as-judge, the
Arq runner and the CI gate are task 6.1). Three pure functions over ranked id
lists, each answering a question the others can't:

* **recall@k** — did we find it at all? A passage that never reaches the
  reranker can never be cited.
* **MRR** — how far down was the first good hit? Catches "found it, at rank 9".
* **nDCG@k** — rewards getting *several* relevant passages high, not just the
  first. The only one that notices when a question has three good sources and
  we surfaced one.

**The labelled set is the part that took the thought.** Labels have to name
chunks, but chunk ids don't exist until ingestion and change on every
reindex — so `retrieval_support_v1.json` labels by an author-written `key`
per corpus passage and the harness resolves keys to real chunk ids
afterwards, keeping the fixture hand-editable. The corpus is built to be
*hard*: several questions share no content words with the passage that
answers them ("get my money back" vs "refund", "dropped my device" vs
"accidental damage"), and the distractors are near-misses by design (refunds
vs exchanges vs store credit, each with its own deadline). Two cases have
more than one right answer, which is the only thing that makes nDCG say
something recall doesn't.

> **A measurement that was measuring nothing, and the fix.** The first run
> scored a clean **1.000 on all three metrics** — which looked like success
> and was actually a broken test. On a 12-passage corpus, `recall@5` hands
> back 42% of everything indexed, so it would have sat at 1.000 straight
> through a substantial regression. Re-scored at **k=1**, where there is real
> headroom, the picture is both honest and genuinely good: **recall@1 0.917,
> MRR 1.000** — the reranker puts the correct passage first on all twelve
> questions, including the paraphrased ones, and the 0.917 is exactly the
> ceiling imposed by the two multi-answer cases (which cannot exceed 0.5 at
> k=1). The assertions now sit on k=1; the k=5 figures are printed for
> context, not asserted on. A number with no headroom tests nothing.

> **Live-verified**: with the config explicitly asking for contextual
> retrieval *and* a real Anthropic key present, a real ingestion through the
> API + Arq worker left `context_prefix` NULL and wrote **zero** Haiku usage
> events — total Haiku spend across the entire database: `0`. The gate holds
> where it matters. The same run then chatted and came back with a proper
> `[1] Escalation Policy` citation, and toggling **Retrieval** in the new
> Subagents panel persisted, projected a `subagent` node onto the graph, and
> raised the `bare_subagent` warning because that assistant had no knowledge
> base — UI hint, validator and runtime gate all agreeing.

Verified: `ruff`/`mypy` clean, **230 passed** total (205 unit + 25
integration — up from 158+22), web typecheck + lint clean.

**Phase 2 is complete.** Every task, P0 through P2: the knowledge base can be
built, filled, wired, tuned, searched, cited, optionally contextualised,
optionally delegated to a subagent — and, finally, *scored*. Next is Phase 3,
database integrations.

---

## 5. Phase 3 — Database integrations  *(complete)*

Phase 2 let an assistant read *documents*. Phase 3 lets it read — and, with a
human's blessing, write — a *database*. That sounds like a smaller step than it
is. A knowledge base is a copy of data we ingested, chunked and control end to
end. A tenant database is **live production data we do not own**, reached with
credentials the user hands us, queried by statements a language model wrote.

Almost every design decision in this phase falls out of that one sentence.

### 5.1 The threat model, stated plainly

Three things can go wrong, and they need different defences:

| Risk | Example | Defence |
|---|---|---|
| **Credential compromise** | someone dumps our Postgres and gets every tenant's DB password | envelope encryption (§5.2) |
| **The model does something destructive** | `DELETE FROM users` because a prompt said so | parse-and-classify guard + human approval (§5.5, §5.7) |
| **The model reads what it shouldn't** | `SELECT * FROM salaries` | permission profile + introspection filtering (§5.6) |

A point worth being blunt about, and it is written into
[`DATABASE_ACCESS.md`](DATABASE_ACCESS.md): **none of this replaces a
least-privilege database role.** Our guard is software and software has bugs; a
`GRANT` is enforced by the database itself. The guard's job is to give *good
error messages* and stop honest mistakes early. The role's job is to actually
protect the data. Both, always.

### 5.2 Envelope encryption — `app/security/crypto.py`

The naive approach is to encrypt each password directly with one key from the
environment. That works until you want to rotate the key, at which point you
must decrypt and re-encrypt every secret in the database.

**Envelope encryption** adds one indirection:

```
password ──AES-256-GCM──► ciphertext      (encrypted with a per-secret DEK)
    DEK  ──AES-256-GCM──► dek_wrapped     (DEK encrypted with the KEK)
    KEK  = APP_KEK from the environment   (never stored)
```

Each secret gets its own random **data encryption key** (DEK). The DEK is
wrapped by the **key encryption key** (KEK) from `APP_KEK`. The row stores the
ciphertext, the wrapped DEK, and the nonce — never the KEK.

Two properties fall out:

- **A database dump is inert.** Without `APP_KEK` the wrapped DEKs cannot be
  unwrapped, so the ciphertexts cannot be read.
- **Rotation is cheap.** `rewrap()` unwraps each small DEK with the old KEK and
  re-wraps it with the new one. The bulk ciphertext is never touched.

Two smaller details that matter more than they look:

- **AAD binding.** The secret's id is passed as *additional authenticated
  data*. GCM authenticates it without encrypting it, so a ciphertext lifted
  from one row and pasted into another fails to decrypt. Without AAD, swapping
  two rows silently swaps two tenants' credentials.
- **`SealedSecret.__repr__` redacts.** The single most common way secrets leak
  is not cryptography, it is a log line or a traceback printing an object. The
  repr returns a placeholder, so even `log.info("...", secret=s)` is safe.

No endpoint can return a password: `DbConnectionSummary` has no field that
could carry one, only a `has_password: bool`.

> **Live-verified.** With a connection created through the UI, the plaintext
> password appears in **zero** columns across every `text`/`varchar`/`jsonb`
> column in the entire schema (checked by iterating
> `information_schema.columns`). The stored row is 12 bytes of nonce, a 48-byte
> wrapped DEK, and ciphertext exactly 16 bytes longer than the plaintext — the
> GCM authentication tag.

### 5.3 Adapters — `app/datasources/`

Four engines: Postgres, MySQL/MariaDB, SQLite, MongoDB. Each needs
**introspection** (what tables exist?) and **execution** (run this, safely).

The first design mistake to avoid: a single `DbAdapter` protocol with a
`run(sql)` method. MongoDB genuinely is not that shape — it has no statement to
parse or guard. Forcing it in means either a `run()` that raises
`NotImplementedError` (a runtime surprise) or a Mongo-shaped hole in every SQL
code path.

So the protocol is **split**:

```python
class DbAdapter(Protocol):      # test(), introspect() — every engine
class SqlAdapter(DbAdapter):    # + run(statement) — engines that take SQL
```

`sql_guard` and the `sql_*` tools are typed against `SqlAdapter`. Handing them
a Mongo connection is now a *type error*, not a 3am traceback.

**Normalization** (`normalize.py`) is the other half. Every driver returns its
own types, and the rows go straight into a model's context as JSON. The rule
that matters most: **`Decimal` becomes a string, never a float.** Money is the
most common decimal column in any business database, and `0.1 + 0.2` is the
wrong number to show someone. Dates become ISO-8601, UUIDs and bytes become
strings.

**Pooling** (`pool.py`) keeps one pool per connection, because opening a TCP
connection and authenticating per query turns a 40ms question into a 400ms one.

### 5.4 Two layers of "read-only", not one

SQLite has no roles, so the adapter opens the file with `mode=ro` in the URI
unless a write was actually approved. This is defence *underneath* the guard:
even a statement that somehow got past classification cannot write through a
read-only handle.

Both Postgres and MySQL also get a **server-side** statement timeout
(`SET LOCAL statement_timeout` / `MAX_EXECUTION_TIME`), not just an asyncio
one. A client-side timeout gives up on the answer while leaving the query
burning CPU on the tenant's production box.

### 5.5 `sql_guard.py` — parse, don't pattern-match

The tempting implementation is a blocklist of scary words. It does not work:
`SELECT 1; DROP TABLE users` passes a `startswith("SELECT")` check, and
`/*x*/DELETE` defeats a regex.

The guard **parses** with `sqlglot` and reasons about the AST:

1. `_check_raw_text` — cheap textual rejections before parsing.
2. `sqlglot.parse` — **exactly one statement**. Stacked statements are the
   classic way to smuggle a write behind a read, so two trees is a refusal.
3. `_classify` — `read` / `write` / `ddl` from the *node type*, not the words.
4. `_check_constructs` — banned functions (`pg_read_file`, `LOAD_FILE`), banned
   schemas (`pg_catalog`, `mysql`, `information_schema`).
5. `_check_tables` — `allow_tables` (exhaustive when set) and `deny_tables`.
6. `_apply_limit` — inject or tighten a `LIMIT` on reads.

That last one is worth dwelling on: the limit is **injected into the AST**, so
it is enforced by the *server*. Truncating client-side would still make the
database materialise ten million rows and ship them over the network.

> A test-quality note. Mutation testing on `_classify` found that the
> `exp.With` branch is **dead code** — sqlglot attaches a CTE as an argument on
> the `Delete`/`Update` node rather than wrapping it in a `With`, so a
> `WITH ... DELETE` is already classified correctly by the main path. The
> branch stays as version-defensive code, but the comment and the test
> docstring were rewritten to say what is actually true rather than claiming a
> protection that path does not provide. A test that passes for the wrong
> reason is worse than no test.

For MongoDB the equivalent is a pipeline screen: `$out` and `$merge` turn a
read into a write, `$where`, `$function` and `$accumulator` are server-side
JavaScript. All refused before the pipeline reaches the server. And the limit
is **appended** rather than trusted, because a pipeline can `$unwind` its way
well past whatever `$limit` it declared earlier.

### 5.6 Permissions are enforced, not advisory

Per connection: `read` / `write` / `ddl`, `allow_tables`, `deny_tables`,
`row_limit`, `statement_timeout_ms`.

The subtle one is `deny_tables`. It filters **introspection**, not just query
results — a denied table is never introspected, never cached, and never
described to the model. The assistant does not learn the table exists. Hiding a
table only at query time tells the model exactly what to go looking for.

> **Live-verified**: with `deny_tables: ["salaries"]`, the schema endpoint
> returns `['tickets']` and nothing else, and `SELECT * FROM salaries` comes
> back `blocked: table salaries is denied for this connection`.

### 5.7 Human-in-the-loop approvals — the hard part

A read runs unattended. A write stops and asks a human. Three things make this
harder than it sounds.

**(a) Risk comes from parsing, not from the tool name.** `sql_query` is not
inherently dangerous — `SELECT count(*)` and `DROP TABLE users` arrive through
the same tool. Classifying on the name would either prompt for every read
(users click approve reflexively, which is *worse* than no prompt) or wave
through every write. So `can_use_tool` runs the statement through the guard and
only a genuinely mutating one asks.

**(b) The card shows the exact statement.** "Approve a database write?" is
unanswerable. "Approve `UPDATE tickets SET status='closed' WHERE id=4`?" is
answerable. The `rationale` field carries the statement verbatim.

**(c) Every non-approval is a denial.** Timeout, disconnect, expiry,
unparseable — all deny. The only path to `allow` is an explicit human
"approved".

The mechanism: the tool permission callback creates a durable `approvals` row,
emits an `approval_required` SSE event, and **awaits a future**. The resolve
endpoint completes it. An in-process registry handles the common case; Redis
pub/sub covers the case where the decision arrives at a *different* API worker
than the one holding the stream.

> **Two bugs this phase, both found by running the thing rather than by tests.**
>
> **A deadlock.** `Turn.stream()` originally had two queues — driver events and
> a side channel for approvals. It drained the side channel once, then blocked
> on the driver queue; the driver's pump was blocked inside the permission
> callback waiting for an approval that could never be delivered. Classic
> lock-ordering deadlock, in async clothing. Fixed with **one** shared queue
> that both the pump and `emit()` write to.
>
> **A test that could never have caught it.** httpx's `ASGITransport` awaits
> the entire app call before exposing the response, so it **buffers the whole
> stream**. No test going through it can observe a mid-turn event — it would
> report success whether events streamed or arrived in one lump at the end.
> The HITL tests now drive `chat.run_message` directly, and the module
> docstring says why so nobody "simplifies" it back.

### 5.8 Four bugs found by driving the real demo path

Tests were green, `ruff` and `mypy` clean, 350 passing. Then the whole flow ran
against a live uvicorn, a real Postgres and a real browser. Four defects fell
out that no green suite had noticed — each one worth reading as a lesson in
what tests do not cover.

**1. Every write reported "0 row(s) affected".**

`row_count` was `len(rows)` in all three SQL adapters. An `UPDATE` without
`RETURNING` returns *no rows*, so the count was always zero — no matter how
many rows it changed. The string is shown to whoever *just approved the write*,
and "0 rows affected" reads as "nothing happened", inviting them to run it
again.

The fix separates two genuinely different facts: `row_count` (rows returned)
and `affected_rows` (rows changed). Getting `affected_rows` is engine-specific:
MySQL and SQLite expose `cursor.rowcount`, but asyncpg's `fetch()` does not
carry the count at all — only `execute()` returns the command tag
(`"UPDATE 3"`).

That creates a trap. Switching writes to `execute()` would silently discard the
output of `UPDATE ... RETURNING`. So the guard now reports `returns_rows` from
the AST, and the adapter picks its path: a plain write goes through `execute()`
for the tag, a `RETURNING` write is fetched like a read — where the rows
returned *are* the rows affected.

**2. Blocked queries rendered as successes.**

Every capability signalled refusal by returning the string `"blocked: ..."` in
an otherwise ordinary successful result. The SSE `tool_result` therefore
carried `status: "success"`, so the chat UI drew a refused query exactly like
one that ran — and the model was told its statement succeeded.

The SDK has a flag for this (`is_error`), and nothing was setting it. Now a
`_err()` helper marks genuine failures, the fake driver honours it instead of
hardcoding `"success"`, and a test walks the source of all four cap modules to
fail if anyone reintroduces `_text("error: ...")`.

**3. A human was asked to approve writes that could never land.**

With `expose_write=False` on the canvas node, an `UPDATE` still stopped and
asked. The reviewer approved — and was then told `blocked: this connection is
read-only`. Interrupting someone to authorise something that cannot happen is
worse than not asking at all: it is precisely the mechanism by which people
learn to click approve without reading. Approval fatigue is a real failure
mode, and every prompt that cannot matter makes the prompts that do matter less
likely to be read.

`classify()` now takes the wired `DatabaseRef`s and denies outright when the
canvas has not exposed writes — no human involved. The refusal also *names the
switch*: "Turn on 'Expose writes' on the database node". A refusal the user
cannot act on gets reported as a bug, and a model that is not told why simply
retries the same statement.

**4. The canvas said "unknown connection".**

The build page carefully assembled a label map covering both data sources *and*
database connections — the comment even said so — but `graph-sync.ts` only
looked it up for `data_source` nodes. So a correctly configured database node
displayed "unknown connection". One line, and only visible by looking at the
screen.

### 5.9 Two gates, not one

The connection's permission says what the **credential** may do. The canvas
node's `expose_write` says what **this assistant** may ask for. Both must
agree.

This is not redundancy. The permission lives on the connection, while
`expose_write` lives on the published graph — so the graph carries its own,
versioned record of what it was allowed to do. Rolling back to an old version
rolls back its write permission with it, and a second assistant pointed at the
same writable connection does not silently inherit write access.

### 5.10 What "verified" means here

Every claim below was observed against a live stack — uvicorn on :8000, Next.js
on :3000, Postgres/MySQL/Mongo/MinIO/Redis in Docker — not asserted in a test.

A 30-check scripted run over HTTP:

- the connection tests `ok` against the real server; the password never returns
- `deny_tables` hides `salaries` from introspection entirely
- a `SELECT` returns real rows with `LIMIT 50` injected server-side
- a write with `expose_write=False` is refused **without** interrupting a human
- with both gates on, `approval_required` arrives **0.016s** into the stream and
  the connection **stays open 0.77s** while a human decides — over a *separate*
  HTTP connection — which is the only way to prove incremental SSE delivery
  rather than a buffered response
- denied → the write does not run, the database is unchanged
- approved → `1 row(s) affected`, and the row really changed in Postgres
- the same statement asks **again** next time: approval is not cached consent
- `DROP TABLE` is still refused with `write` granted but `ddl` not

And then the same path by hand in a browser: register → add a connection →
`Test` → set permissions → drag `database → agent` on the canvas → publish →
chat. The approval card appeared with a high-risk badge, a ticking 297s
countdown, the exact statement in monospace, and the line *"Nothing runs until
you choose; no answer means denied."* Deny produced a `denied` card and "I
couldn't run that". Approve produced `1 row(s) affected` — and ticket 4 was
`closed` in Postgres, with both the denial and the approval on record in the
`approvals` table.

Verified: `ruff`/`mypy` clean, **350 passed** (307 unit + 43 integration — up
from 293+38), web typecheck + lint clean.

**Phase 3 is complete.** An assistant can now be pointed at a real database,
told what it may see and what it may change, and trusted to stop and ask before
it changes anything — with the exact statement on screen.

---

## 6. Hardening — the Phase 0–3 audit's P0s

Phase 3 ended with "complete" written above. Then an audit
([`PHASE_0_3_AUDIT.md`](PHASE_0_3_AUDIT.md)) put one independent reviewer on
every plan task and a second reviewer on every verdict, and found that
**two headline safety features, the SQL guard and the approval router, did not
hold in the configuration this project would actually ship in.** This section
covers the 15 P0 items: what was wrong, the concept behind each fix, and how
each one was shown to work.

A theme runs through almost all of them, and it is worth naming first:
**the test double hid the bug.** `FakeDriver` called `can_use_tool` by hand, so
every approval test passed while the real SDK never called it. A test proves
what its harness exercises, and nothing more. So every fix below was also
checked against something real: the actual `claude` CLI binary, Postgres,
Redis, MinIO, a browser. The cost was zero API credits, using the trick in §6.3.

### 6.1 The SQL guard: classify by the tree, not the top node (P0-1…P0-3)

`guard()` parsed each statement with sqlglot and looked at the **root** of the
tree: `Select` meant a read. But SQL nests.

```sql
WITH d AS (DELETE FROM users RETURNING *) SELECT * FROM d   -- root: Select
SELECT * INTO newtab FROM users                             -- root: Select
```

Both came back as `kind=read`, ran with no approval, and on the demo database
the first one deleted every ticket (run inside a rolled-back transaction, so
nothing was lost). The fix is `_check_nested_mutations`: walk **every** node,
and refuse any Insert/Update/Delete/Merge/DDL/`INTO` found anywhere below a
read. The one exception is DDL sub-clauses under a DDL root, because
`ALTER TABLE … ADD CONSTRAINT` legitimately contains DDL-shaped children.

Two more holes had the same root cause (checking one shape and missing
the others):

- **Set operations got no `LIMIT`.** `SELECT … UNION SELECT …` has a `Union`
  root, not a `Select`, so the row cap was never injected. `_apply_limit` now
  covers every read type, and a query's own smaller limit (including
  `LIMIT 0`) is kept rather than raised.
- **System catalogs were reachable unqualified.** `pg_shadow` without
  `pg_catalog.` slipped past a schema-qualified deny list. The rule is now
  name-based: an unqualified `pg_*` counts as a catalog on Postgres,
  `sqlite_*` on SQLite, and `pragma_*` table-valued functions are banned
  outright. Functions with side effects (`nextval`, `set_config`,
  `pg_terminate_backend`, `load_extension`, …) are banned even inside a
  `SELECT`, because a read that changes state is not a read.

The router also changed. A statement that **every** dialect refuses, even with
every permission switched on, is now denied without asking anyone
(`_guard_under_any_dialect`). No human decision can make it run, and asking
anyway is approval fatigue: the reviewer approves, then learns it was blocked.

**Proving the tests carry weight.** 81 guard tests pass, but passing tests
only show that the code agrees with the tests. So the guard was *mutated*: ten
deliberate breakages, such as deleting the nested walk or loosening the limit
comparison, with the suite run against each. All ten were caught. A mutation
that survives marks a test that asserts nothing.

### 6.2 The approval router never fired on the real SDK (P0-4)

The SDK has two lists with confusingly similar names:

| Option | What it actually means |
|---|---|
| `tools` | which built-in tools **exist** for the model |
| `allowed_tools` | which tools are **auto-approved**, skipping `can_use_tool` |

We put every enabled tool, `sql_query` included, into `allowed_tools`,
reading it as "these are the allowed ones". The SDK read it as "never ask about
these". So the permission callback, the approval card and the "nothing runs
until you choose" promise were all bypassed with a real key. The SDK even has a
`CanUseToolShadowedWarning` for exactly this situation.

The fix separates the two jobs:

- `tools=builtin_tools(spec)`: an explicit allow-list of built-ins. Left
  unset, the CLI offers everything it ships, which was 18 built-ins including
  `CronCreate` and `SendMessage`. The live count of tools offered dropped
  from 23 to 5.
- `allowed_tools=[]`: nothing skips the router.
- A `PreToolUse` hook (`build_tool_gate`) with `matcher=None`, which fires on
  **every** call and denies any tool not permitted for this assistant.
  `can_use_tool` only runs when the CLI *would* prompt, while a hook always
  runs, so the hook is the backstop.

`RuntimeSpec.allowed_tools` was renamed `enabled_tools` so the name can never
suggest the SDK meaning again.

### 6.3 Checking the real driver without spending credits

The real SDK driver spawns the bundled `claude` CLI, which talks to the
Anthropic API. The CLI honours `ANTHROPIC_BASE_URL`, so a small FastAPI app
that speaks the Messages API's streaming format stood in for it. That app
scripts tool calls, text and usage. A second uvicorn on :8001 ran with
`AGENT_DRIVER=claude` set **in that process only**, a dummy key and an empty
`CLAUDE_CONFIG_DIR`, so `.env` stayed pinned to `fake`. Every real-driver
claim in this section was observed this way: the real CLI, the real SDK
control protocol and the real permission callback, with $0 billed.

### 6.4 Streaming, tool results and usage on the real driver (F-2, F-9)

Three things the fake driver faked:

- **Tool results.** The SDK returns them inside a `UserMessage`, the model's
  view of the conversation, where the tool's output is "user" input. The
  driver only read `AssistantMessage`, so the UI never saw a result on the
  real path. `_events_for` now handles both, honours `is_error`, and marks
  results for calls the router denied as `denied`.
- **Token streaming.** With `include_partial_messages=True`, the SDK emits
  `StreamEvent` deltas. `_PartialText` turns them into tokens as they arrive,
  so the first token showed at 1.5s instead of with the whole answer, and it
  skips the finished block when it arrives, so nothing is printed twice.
- **Usage as it happens.** Cost used to arrive only in the final
  `ResultMessage`, so the budget check fired after the money was spent.
  `_UsageLedger` reports usage per model call (keyed by message id, so a
  repeated message isn't double-counted), then `settle()` reconciles to the
  SDK's authoritative total at the end.

That enables the budget fix (F-9). `max_budget_usd` on an assistant is a
**per-conversation** cap, but the SDK enforces its own `max_budget_usd`
**per query** and knows nothing about earlier turns. We passed it the full cap
every turn, so a conversation with a cent left could spend another full cap.
`turn_budget(cap, remaining)` now hands the SDK the tighter of the two. In the
test, a conversation with $0.30 left and $0.10 per model call stops after the
third call, instead of streaming all ten and noticing afterwards.

### 6.5 A disconnect must not lose the turn (F-1)

When a browser tab closes mid-answer, Starlette stops iterating the SSE
generator. What happens next depends on how it stops:

- `aclose()` raises **`GeneratorExit`** at the `yield`. That is not an
  `Exception` and not a `CancelledError`, so neither `except` clause caught it.
- A cancelled task raises **`CancelledError`**, and under anyio that
  cancellation is **level-triggered**: every `await` inside the cancelled scope
  raises again. A cleanup `await session.commit()` in a `finally` therefore
  gets cancelled too.

So a disconnect skipped the assistant message, the `Run` row, the usage event
and the cost rollup. The spend was real and nothing recorded it. The fix has
three parts:

1. `async with aclosing(turn.stream())`, so the inner generator is closed
   deterministically rather than whenever garbage collection gets to it.
2. Catch `GeneratorExit`/`CancelledError`, and run `_finalize` inside
   `anyio.CancelScope(shield=True)`. The shield makes the cleanup awaits
   immune to the cancellation that is unwinding the task. The normal-path
   finalize is shielded too, because a disconnect can land exactly at its
   commit.
3. A pending approval is **expired** on the way out, again shielded, so a
   reload doesn't offer the reviewer a statement that can no longer run.

The pump shutdown in `Turn.stream` is bounded with
`anyio.move_on_after(PUMP_SHUTDOWN_TIMEOUT_S, shield=True)`, so a CLI that
won't exit can't hold the request open indefinitely. Verified on both drivers:
the run is `aborted` with "client disconnected", and the number of CLI child
processes went 0 → 1 → 0, so none leaked.

### 6.6 Stop (F-3)

Interrupt didn't exist anywhere. Now:

- `POST /conversations/{id}:interrupt` (202) reaches the turn through
  `app/agent/interrupts.py`: a per-process registry plus Redis pub/sub,
  because the turn may be running on a different worker than the one that
  received the click.
- `Turn.interrupt()` sets an event. The real driver relays it to the SDK's
  own `client.interrupt()`, which ends the turn **cooperatively**, so the
  usage the model really incurred is still reported. That took 0.11s live.
  A driver that ignores it is cancelled after `INTERRUPT_GRACE_S`.
- Either way the turn ends *normally*: persisted as `aborted`/"interrupted",
  keeping the partial answer, and the stream ends with `done`.
- The chat UI has a Stop button, and an `AbortController` cancels the fetch
  when the page unmounts.

One gap turned up only on the live stack, after the tests passed. **Stop
while an approval is pending** took exactly 5.15s, the grace period. The
driver is parked *inside* the approval wait, so it can't notice anything, and
the turn only ended when the force-cancel fired. `_raise_approval` now races
the reviewer's decision against the turn's `interrupted` event. Stop settles
the wait as `"interrupted"`, the approval row is expired, and the tool call is
denied with "The user stopped the turn before this was approved." Live it now
takes 0.2s. The regression test raises the grace to 30s, so only this path
can make it pass, and a mutation that disconnects the watcher makes it fail.

### 6.7 Mongo credentials and permissions (P0-5)

Two separate holes:

- **The connection string sat in plaintext** in `options`. A Mongo URI
  usually carries the password (`mongodb+srv://user:pass@…`), so it is a
  credential. It is now its own sealed secret (`SecretKind.db_connection_uri`,
  referenced by `uri_secret_ref`), opened only inside `connection_info` when a
  connection is actually made. Credential-shaped keys are stripped from
  summaries, and a replaced secret is deleted rather than orphaned. The UI
  field is masked and says "never shown again". Verified live: a marker
  string in the URI appeared in **zero** columns of `db_connections` and
  `secrets`.
- **The permission profile was ignored.** `caps_mongo` now checks every
  collection an operation touches, `$lookup`/`$unionWith` targets included,
  against allow/deny lists. It also blocks cluster-level stages
  (`$currentOp`, `$listSessions`, …) and bounds `limit`.

The connection pool keyed clients by host and database only, so two
connections with different credentials to the same host could **share a
client**. The key now includes a hash of the options.

### 6.8 Ownership: who may do what to whose things (F-8, F-5)

- **Role escalation.** Any admin could promote themselves to owner and then
  demote the founder. `_authorise_role_change` now applies rank rules: you
  can't grant a role above your own, can't touch someone who outranks you,
  and can't change your own role at all.
- **Conversations had no owner check.** Any org member could rename, archive,
  post to or stop anyone's conversation. `ConversationEditorCtx` restricts
  that to the creator or an admin, and rename/archive now write audit entries.
- **Archive was a no-op.** It set a status that nothing read. Archived
  conversations now leave the list, and posting to one returns
  `409 conversation_archived`.

### 6.9 Deleting an assistant (F-4)

There was no delete. `DELETE /assistants/{id}` now removes the rows (FK
cascades), the stored secrets, then **after the commit**, best-effort: MinIO
objects, Redis session keys and scratch directories. The order matters. The
database is the source of truth, so if object cleanup fails the worst case is
an orphaned blob, never a row pointing at a missing file. `usage_events` FKs
became `SET NULL`, so billing history survives deletes.

That last change exposed a trap in the unit tier: **SQLite ignores foreign keys
unless `PRAGMA foreign_keys=ON` is set per connection.** Cascades "worked" in
Postgres and did nothing in tests. A connect-event listener in
`db/session.py` now turns it on.

### 6.10 References checked against what exists (F-6, F-10)

The graph validator is pure on purpose, with no database access, so it never
checked what a node *points at*. A `database` node could name a connection
that didn't exist, belonged to another assistant, or wasn't even a UUID. That
last case published cleanly and then raised `ValueError` at the start of every
turn. `services/graph_refs.py` resolves every reference against **this
assistant's** rows and reports problems as ordinary graph errors on the
offending node: `unknown_connection`, `unknown_data_source` and
`expose_write_without_write`. Drafts still save, so work in progress is never
lost, but publishing is refused.

Because a connection's permissions can change **after** publish, the router
also checks the live credential (`credential_allows`) before asking anyone to
approve a write.

On the canvas, "Expose writes" used to be merely dimmed on a read-only
connection, while the code comment said it was disabled. It is now genuinely
disabled when off. When it is already *on* (because the connection was
locked down later), it stays clickable so it can be turned off, since that is
what clears the error, and the hint turns red to explain why publishing is
blocked. Checked in the browser: a real click on the locked switch did
nothing, and turning off the stale one saved and cleared
`expose_write_without_write` on the server.

### 6.11 Models and migrations must agree (F-7)

The HNSW (vector) and GIN (full-text) indexes were created with raw
`op.execute` in a migration and never declared on the `Chunk` model. So
**`alembic check` failed**, and the next `alembic revision --autogenerate`
would have emitted `drop_index` for both. Retrieval would then have become a
sequential scan, with nothing to show why.

They are now declared on the model with
`Index(..., postgresql_using="hnsw", postgresql_ops={...})`, wrapped in
`.ddl_if(dialect="postgresql")` so `create_all` doesn't try to build them on
SQLite. `migrations/env.py` gets an `include_object` filter that skips an
index tagged `info["only_on"]` when comparing against another dialect.
`alembic check` is now enforced twice:

- in the unit tier on SQLite (`test_models_and_migrations_agree`), so CI
  catches a model change shipped without its migration;
- in `scripts/check.ps1` against the dev Postgres, the only place the vector
  indexes exist.

Both halves were mutated: remove the model declaration and Postgres fails;
remove the dialect filter and SQLite fails.

### 6.12 Smaller fixes

- **422 responses leaked the request body.** Pydantic puts the offending
  `input` in each error, which can include a password someone just typed. It
  is now dropped. `ctx` can hold an exception object that plain JSON can't
  serialize, which had turned some 422s into 500s, so it now encodes with
  `custom_encoder={BaseException: str}`.
- The approval tests used a made-up connection id. Once the router started
  checking the live credential they correctly got "no credential, deny", so
  they now create a real writable connection. A stale fixture that the product
  rightly rejects is a test bug, not a product bug.

### 6.13 What "verified" means here

- Every P0 was reproduced **before** fixing (the CTE delete, the unapproved
  write landing, the plaintext URI, the admin takeover) and re-run after.
- Real-driver behaviour was checked through the actual CLI against the fake
  Messages API (§6.3). No credits were spent.
- The browser checks were: assistant delete (confirmation text, row gone,
  `assistant.delete` audited), both states of the "Expose writes" toggle, and
  the masked Mongo field.
- Stop, rename and archive were checked live over HTTP against :8000, the same
  calls the chat page makes. The browser pane was hidden during that part of
  the session, so those three buttons still need a click-through by hand.
- Mutation testing on the guard, the migration check, the budget handoff and
  the stop-during-approval path.
- `.\scripts\check.ps1`: ruff, mypy, unit tier, integration tier,
  `alembic check`, web typecheck and lint.

---

## 7. The "never checked by anyone" list

The audit's second list was things no task owned, so no reviewer ever looked
at them. None of them was a security hole. Several were the kind of gap that
stays invisible until it costs a day: a layout that rearranges itself, a list
that slows down every week, a type that silently disagrees with the server.
This section goes through them in the order they were fixed, and includes
what fixing them turned up along the way. Each item in the audit had a bug
hiding next to it.

### 7.1 A Panels save rearranged the canvas

The canvas mints short node ids (`db`, `ds`, `kb-2`). `project_config`, which
rebuilds the graph from the config on every Panels save, derived its own ids
(`db:{connection_id}`) and looked up saved positions **by id**. So the first
Panels save found no match for any canvas-made node: it renamed each one and
snapped it to a default position. It also rewired edges to its own canonical
layout, so a database wired through a subagent got moved straight onto the
agent.

**Concept: identity, not name.** Two systems that each mint their own ids for
the same thing cannot match on id. The fix matches a node by *what it is*:
its type plus whatever it references (`_identity`: connection id, source id,
tool key, and so on). A matched node keeps the user's id and position. The
same idea covers wiring. `_keep_user_wiring` keeps the edges the user drew
whenever they compile to exactly the same config, and falls back to the
canonical wiring only when they don't. Correctness is checked by compiling
the result, not assumed.

This also made the version diff (§7.5) meaningful. Before it, any Panels save
between two versions showed up as "node removed, node added".

### 7.2 Every list is paginated, by keyset

Plan §8 says all list endpoints are paginated. Only the audit log was, by
`OFFSET`, and the conversation detail inlined *every* message ever sent.

**Concept: keyset vs offset.** `OFFSET 50` means "skip 50 rows", and rows
keep arriving. A message that lands between page 1 and page 2 pushes the
last row of page 1 onto page 2, so the reader sees it twice. Keyset
pagination remembers the sort key of the last row seen and asks for rows
strictly after it, e.g. `(created_at, id) < (:t, :id)`. That's stable under
inserts, and an index seek instead of a scan. Two details make it correct:

- **The key must be a total order.** `created_at` alone ties, so every key
  ends in the primary key. The tie test sets *every* row to the same
  timestamp and still walks each row exactly once.
- **The cursor is not a capability.** It's base64 of the last row's key
  values, but the query that consumes it still carries the caller's tenancy
  filter. A cursor taken from another assistant's list can at worst skip
  rows the caller could already see.

`app/db/pagination.py::keyset_page` does this for every list, returning
`{items, next_cursor}`. Messages page *backwards* from the newest, but each
page is oldest-first, so the chat can prepend a page as it is. The chat keeps
its scroll position when it does.

**A bug the tests found:** the page walk never ended on SQLite. SQLite has
no timestamp type and stores text in whatever format wrote it.
`CURRENT_TIMESTAMP` writes `2026-09-23 11:43:42`, while a bound Python
datetime writes `2026-09-23 11:43:42.000000`, and compared as text the first
sorts *before* the second, though they're the same instant. So the boundary
row compared as "after itself" and came back on every page. The fix compares
`julianday(...)` on SQLite, in `ORDER BY` *and* `WHERE`: they must agree on
the order. Postgres has real timestamps and never had the problem. That's
exactly why the unit tier needed to be the one to catch it.

The same one-second resolution later made a runs test flaky: two runs in the
same second came back in random order. `Run.created_at` now gets
microseconds from Python, as `Message` already did.

### 7.3 `runs.trace_id`, and runs are readable

The plan specified `runs.trace_id` so a turn could be found in the tracing
backend. It was never added, and nothing ever read a `Run` row.

- Every turn gets a trace id. It's the active OpenTelemetry trace when there
  is one, and otherwise a minted id in the same W3C format (32 hex digits).
  It's stored on the run, sent in the `done` event, and **bound into
  structlog's context** for the whole turn. The driver and tool tasks copy
  that context when they're created, so every log line the turn writes
  carries it. A test proves the id reaches a spawned task.
- `GET /conversations/{id}/runs` (filterable by message) and
  `/runs/{run_id}` (with the tool calls as steps) read them back. Each answer
  in the chat has a "Run details" toggle: status, model, tokens, cost,
  duration, trace id.

### 7.4 Invites work end to end

Both endpoints existed. Nothing lived at the URL the API put in the invite
link, there was no way to create an invite from the UI, and accepting one
returned no org id to switch to.

- `GET /invites/{token}` shows what an invite is for *before* sign-in, so the
  invitee knows which email to use. It's unauthenticated on purpose: the
  token is 32 random bytes and single-use, so holding it *is* the
  authorisation.
- Accept commits before responding. It relied on the request teardown's
  commit, which runs *after* the response, so the UI's immediate `/orgs`
  reload could miss the new membership. That's the same race `register` had
  in Phase 2.
- **The web:**
  - an invite page with every state (signed out, wrong account, expired,
    used);
  - a Members page whose invite form shows the link once (the server keeps
    only a hash);
  - login and register honour `?next=` so the invitee comes back to the
    invite.
- **`?next=` is an open-redirect risk**, so `safeNext` only accepts a path on
  this site. `https://evil…`, protocol-relative `//evil…` and backslash
  variants are refused.
- **A bug the browser found:** `?next=` was lost between the login and
  register pages. The page read `window.location` during render, and on a
  client-side navigation the new page renders *before* the address bar
  changes, so it read the previous URL. `useSearchParams()` reads the URL
  being rendered.

### 7.5 Version history and diff

The diff endpoint had existed since task 1.2, with no caller. The build page
now has a **History** tab: pick any two versions to see config changes by
dotted path and graph changes by node and edge. The graph diff also returns
each node's type, so the diff can say "tool node added" instead of a bare
`tool:datetime`. Publish now asks for a note, which is what History shows.
A refused publish used to vanish into an unhandled promise rejection (the
button simply did nothing); it now shows the reason.

### 7.6 Generated API types, and a live `packages/shared`

`apps/web/lib/api.ts` was hundreds of lines of hand-written types copying the
Pydantic models. Nothing checked the copy, and `packages/shared` was never
installed, linted or typechecked by anything.

**The pipeline:**

```
Pydantic models ──► app.openapi() ──► packages/shared/openapi.json   (committed)
                                           │
                     openapi-typescript    ▼
                                    packages/shared/src/api.gen.ts   (committed)
                                           │
                                           ▼
                           apps/web/lib/api.ts: type Run = S["RunOut"] …
```

**Two guards keep it honest:**

- a pytest snapshot fails when the live schema and the committed
  `openapi.json` differ, and names the changed paths and schemas;
- `npm run gen:check` fails when the TypeScript lags the JSON.

Both run in CI and `check.ps1`. The export is normalised to depend on code
only (the app title comes from settings), and a test proves it's
deterministic, because a snapshot of a non-deterministic export would fail at
random.

`Graph` and `AssistantConfig` stay deliberately loose in the web, since the
canvas and panels treat them as open records. A compile-time check pins them
to the generated types anyway: if the loose shape ever demands something the
API doesn't send, `tsc` fails. That check was mutation-tested.

**What switching over revealed** is the real payoff. The compiler flagged
three places where the API contract itself was wrong:

1. `DbConnectionSummary.permissions` was `dict[str, Any]`. It published no
   shape, so the hand-written TS had invented one.
2. `DbSchemaOut.schemas` was an untyped list of dicts. It's now
   `DbNamespaceOut → DbTableOut → DbColumnOut`, mirroring the introspection
   dataclasses. That also made `sql_list_schemas` work on typed objects.
3. **Fields the server always sends were marked optional.** Pydantic leaves
   any field with a default out of `required`, response schemas included.
   `json_schema_serialization_defaults_required=True` (on `ApiModel`,
   `ORMModel` and the config/graph bases) makes them required in *response*
   schemas only; a default in a request still means "may be omitted".
   Models used both ways now publish `X-Input` and `X-Output` schemas, which
   is the truthful description.

### 7.7 CI builds the images, and the audits can fail

Neither Docker image had ever been built, and both audits ended in
`|| true`, so a critical advisory could never turn CI red.

- **A `docker` job** builds both images and then *starts* them: the web
  image must serve `/login`, and the API image must pass `/healthz` with no
  database or Redis (its Redis listeners degrade to retrying, by design). A
  build that succeeds says nothing about whether the entrypoint works.
- **The audits now fail the build.** Before removing `|| true`, both were
  run locally. `pip-audit` was clean. `npm audit` had a real finding: Next
  15.5 pins its own nested `postcss@8.4.31`, with two high advisories
  (arbitrary file reads through source maps). npm's suggested fix was a
  major upgrade to Next 16. The smaller real fix is an `overrides` entry
  that moves that nested copy onto the patched `postcss@8.5.26` the app
  already used. `npm ls` calls it "invalid" (an npm quirk with overridden
  exact pins), so it was checked with a clean `npm ci` and a full
  `next build`.
- **Building the images found two more things.**
  - `.env` had comments on the same line as values
    (`APP_ENV=dev   # dev | test | production`). python-dotenv strips those;
    `docker run --env-file` does not, so the container read the comment as
    part of the value and refused to start. Comments now sit on their own
    lines.
  - The API image apt-installed Node, npm and curl "for the Claude Code
    CLI". The SDK ships that CLI inside the wheel as a native binary, which
    ran fine with Node removed from the PATH. Dropping the layer saved
    420 MB.
- **A gap left open:** Python dependencies have no lockfile, so two image
  builds can resolve different versions (the bundled CLI moved from
  2.1.280 to 2.1.281 between two builds an hour apart). Pinning, e.g. with
  `uv lock` or a constraints file, is the fix, and belongs with release
  work.

### 7.8 Two things outside the list

**Host ports below 49152.** After a reboot Postgres couldn't start: port
`55432` sat inside a block Windows had reserved. Hyper-V/WinNAT reserves
random blocks of the dynamic port range (49152–65535) at every boot, and
every datastore host port was inside it. They moved to 45432 / 46379 /
43306 / 47017, with the reason written next to them in
`docker-compose.yml`. The volumes were untouched, so the data survived.

**Every border in the app was the same gray.** Reported from the UI:
inputs and scrollbars looked like harsh gray boxes. The root cause was in
`globals.css`: `* { border-color: … }` sat outside any CSS layer. Tailwind's
utilities live in `@layer utilities`, and **unlayered CSS beats layered CSS
regardless of specificity**. So no `border-*` colour class anywhere had ever
applied, and four intended indicators were silently dead:

- the red border on an invalid canvas node;
- the drag-over highlight on the sources drop zone;
- the amber border on a pending approval card;
- the hover tint on assistant cards.

The global rules now live in `@layer base`. On top of that:

- **Form fields** got their own tokens: a faint fill, a soft edge that firms
  up on hover, and a focus state in the primary colour with a soft halo.
- **Selects** got a themed chevron in place of the OS arrow.
- **Scrollbars** are thin and low-contrast in both themes.
- **Autofill** no longer paints a white box in dark mode.

### 7.9 What "verified" means here

- **Browser:**
  - 52 assistants page as 50 plus "Load more", with no duplicates;
  - a 52-message chat opens on the latest 50, and "Load earlier" restores
    the rest without jumping the scroll;
  - run details show the real run row;
  - the invite flow: Members page → link → signed-out view → wrong-account
    view → sign-out keeps `?next=` → join lands in the new org;
  - publish with notes, then compare in History;
  - a Panels save moved no canvas node.
- **Live Postgres:** pagination (including forced timestamp ties),
  migrations at head, `alembic check` clean.
- **Images:** both built and started.
- **Mutation tests:** identity matching, wiring preservation, the id
  tie-breaker, the trace-id binding, the stale-types check and the loose
  graph check.
- **Not verified by sight:** the input focus ring. With the app window
  unfocused, `:focus-visible` never matches, so it was checked as compiled
  CSS rather than on screen.

## 8. Closing the audit's partial gaps

The audit marked most tasks "partial": the headline feature worked, but a
piece of the plan's wording for it had been cut, stubbed or never wired.
This section goes through those pieces phase by phase. As in §7, fixing them
kept turning up real bugs next to them, and those are called out as
**Found on the way**.

### 8.1 Phase 1: the runtime

**Hooks (1.10).** The PreToolUse gate moved into `app/agent/hooks.py`
(`build_hooks`). The module docstring maps each hook duty in the plan to
where it is actually enforced:

- output capping and secret stripping in `post_tool.run_capability`, the one
  path every capability's result takes;
- recording in `_finalize`;
- "finalize on Stop" by the shielded finalize, which also covers a client
  disconnect, where a Stop hook would never run.

**Observability (1.9).** A turn is now a span tree:

```
chat.turn              prompt in, answer out, status
├── claude             a generation: model, tokens, cost
└── tool:<name>        one per tool call, with its real start and latency
```

Its attributes follow Langfuse's OpenTelemetry mapping (`langfuse.*`,
`gen_ai.*`). Langfuse gets only these spans; a generic OTLP collector gets
everything, including HTTP and SQL spans.

**Concept: the one process-wide provider, two exporters.** One
`TracerProvider`, two span processors. The Langfuse processor filters by
instrumentation scope, and makes a span a root when its parent isn't one it
exports. Otherwise the turn would hang off an HTTP span Langfuse never
receives.

- **Children written at finalize.** The turn span's children are created at
  finalize, with explicit start and end times. The turn is a generator that
  a client disconnect can close from another task, and an OTel context
  attached in one task cannot be detached in another.
- **The compose stack moved to Langfuse v3.** The first version with an
  OTLP endpoint; the v2 we pinned could not receive spans at all. v3 needs
  ClickHouse plus its own Postgres, Redis and S3. MinIO no longer publishes
  Docker Hub images, so the Langfuse stack uses Chainguard's build.
- **Logs.** structlog now masks credential-named fields and
  credential-shaped values in every line.
- **The worker.** It now configures logging and tracing at all; it did
  neither before.

**Found on the way.**

- **The pub/sub listeners dropped messages about two-thirds of the time.**
  They shared the command client, whose 1-second socket timeout fired on
  every quiet second. Each listener then dropped its subscription, slept 2 s
  and resubscribed, and pub/sub keeps nothing for an absent subscriber. So a
  cross-worker stop or approval decision sent in that window was lost. They
  now poll `get_message` on a dedicated client (`subscribe_forever`); the
  old loop, reproduced in a test, loses 2 of 3 messages.
- **`OTEL_EXPORTER_OTLP_ENDPOINT` was passed to the exporter as-is.** By
  spec it's a base URL (`/v1/traces` is appended only when the SDK reads the
  variable itself), so spans went to the collector's root and 404'd.

**A real driver test (1.11).** `test_driver_transport.py` plugs a scripted
CLI into the real `ClaudeSDKClient` through a new `transport_factory` seam.
The SDK's own parser and control protocol then run for real:

- `initialize`, carrying our PreToolUse hook;
- `hook_callback`;
- `can_use_tool`, where a refusal is labelled "denied";
- `mcp_message`, with the full MCP handshake and a real call into the
  calculator capability;
- `interrupt`.

A CLI that dies mid-turn becomes an `agent_error`.

**The session cache (1.7).** It was written only when the session id
*changed*, so an eviction or Redis restart emptied it for good. Worse, the
turn resumed `cached or row`: a cache write that failed during a Redis blip
left the old id in place, and the next turn resumed the older session. Now
the row decides, and the cache is written after every commit (never ahead of
the row, refilled after a miss, its TTL counting from last use).

**The web (1.3, 1.8).**

- **Tests.** vitest now covers `safeNext`, the graph-sync helpers, formatting
  and the refresh logic.
- **Spend meter.** A cost meter sits above the chat: it ticks up from live
  `usage` events and settles to the server's figure, which includes
  retrieval.

**Found on the way.**

- **`safeNext` was an open redirect.** Browsers strip tabs and newlines
  while parsing a URL, so `/\t/evil.example` passed the old string checks
  and navigated to `//evil.example`. It now parses the target the way the
  browser will, and requires the same origin.
- **Nobody ever called `/auth/refresh`.** A 401 was just an error, so
  every user was signed out 15 minutes after signing in, with a valid 30-day
  refresh token unused in storage. Now `authedFetch` refreshes once and
  retries. Refresh is single-flight within a tab, and serialised across tabs
  with a Web Lock. A tab that waited finds the token already rotated and
  uses the new one; presenting the spent token would count as reuse, and the
  API would revoke the session.

### 8.2 Phase 2: RAG

**Chunking (2.3).** When there was no space after the overlap point,
`find` returned -1, and `nxt + 1 == 0` passed the bounds check. So in any
text of newline-separated tokens, every chunk started at offset 0 and
re-embedded the document from its beginning. The snap now looks for any
whitespace, and only within the overlap the chunk borrowed.

**Ingestion (2.5, 2.6).**

- **Stable ids.** The document id derives from the source, and a chunk id
  from `(document_id, ordinal, content)`, as the plan specifies. A reindex
  of unchanged content writes the same ids back, so a citation in an old
  answer still resolves.
- **Nothing paid for twice.** Before the old chunks are dropped, their
  vectors and context lines are kept by `(ordinal, content)`:
  - a context line is reused only if the document's checksum is unchanged,
    since it describes the chunk's place in *that* document;
  - a vector is reused if the model and the exact embedded text match.
  Reindexing an unchanged source costs nothing ("0 embedded, 12 reused · no
  cost"). An edit re-embeds only what changed.
- **Contextualization.**
  - *Batched:* 8 chunks per call, so the document excerpt goes once per
    batch rather than once per chunk.
  - *Retried:* rate limits and overload are retried with `Retry-After` or
    jittered backoff.
  - *Budgeted:* estimated before it runs; over `INGEST_CONTEXT_BUDGET_USD`
    the source is indexed without context lines, and the report says why.
- **Progress.** Progress goes to Redis, not the database. A reindex writes
  its chunks in one transaction, so nothing can be committed from inside it.
  The sources list attaches the latest progress to a processing source
  ("Embedding 32/47").
- **Report and metadata.** `data_sources.ingest_report` records what each
  run did. `Document.mime` and `checksum` are finally filled in.
- **Queueing failures.** A source whose job could not be queued is marked
  failed with a "use Reindex" message, instead of sitting at "pending" with
  nothing ever coming for it.

**Found on the way.**

- **Voyage's per-request limit.** Every chunk went to Voyage in one request.
  Voyage refuses more than 120K tokens per request, so a source past about
  150 pages could never be indexed. Requests are now batched within the
  limits and retried.
- **Arq's 300-second default timeout.** Arq cancels a job at 300 s, and a
  47-chunk source on the CPU embedder took longer. Cancellation isn't an
  `Exception`, so the source stayed "processing" forever. Ingestion now has
  its own timeout, and a cancelled job marks its source failed.
- **Windows console logging.** On a cp1252 console, arq's `→` raised
  `UnicodeEncodeError` inside logging. stdout now escapes what it can't
  encode.

**Knowledge-base tools (2.8).**

- `kb_search` takes `source_ids`, narrowed to what the assistant allows.
- Results carry `score` and `uri`.
- `kb_list_sources` shows document and chunk counts and respects the
  allow-list.

**Subagents (2.10).** The driver ignored `parent_tool_use_id`, so with
`forward_subagent_text` the retrieval subagent's working notes streamed and
were saved *as the answer*. Now:

- Events carry a `parent_id`, and the runtime keeps a subagent's text on its
  delegation call.
- The chat nests the subagent's calls under that call's card.
- A subagent's model calls still cost money, but no longer count toward the
  main agent's `max_turns`.
- The subagent model role's `max_turns` and `effort` reach the SDK. A
  subagent node's `model` and `max_turns` are compiled as overrides instead
  of being dropped; they default to "inherit", so a config round-trips
  unchanged.
- FakeDriver can now delegate, so all of this runs offline.

### 8.3 Phase 0: foundations

**Tooling (0.1).**

- **`npm run format` would have reformatted the ignored files.** The script
  passed `--ignore-path .gitignore`, which in Prettier 3 *replaces* the
  default list, so `.prettierignore` was dropped and the API code and docs
  would have been reformatted. It now names both files.
- **A pre-commit config now exists.** It is check-only.
- **The ruff hooks run without `--config`.** With `--config`, ruff resolves
  `src` and `per-file-ignores` against the working directory rather than
  `apps/api`.
- **Formatting is enforced.** `format:check` now runs in `check.ps1` and CI.

**Readiness (0.3).** `/readyz` checks the database, Redis, storage and (for
the real driver) the Claude CLI, concurrently, each within 2 s.

- **Critical checks.** Database or CLI down means 503.
- **Degradable checks.** Redis or storage down means 200 "degraded". Chat
  still works then; pulling every instance out of the load balancer would
  turn a degraded service into none.
- **Probe client.** The storage probe has its own client that gives up in
  2 s. boto's default retries for a minute and would pile up threads under
  a frequent probe.

**UI primitives (0.4).**

- **Dialog:** a modal on the native `<dialog>` element (inert background,
  focus containment, Esc), with promise-based `useConfirm` / `usePrompt`
  replacing every `window.confirm` / `prompt`. Focus lands on Cancel, so
  Enter never confirms a destructive action by accident.
- **Toast:** announced through a live region.
- **Tabs:** follow the ARIA tabs pattern (roles, one tab stop, arrow keys).
- **Theme toggle:** new.

**Org isolation (0.7).** Tenant scoping was a convention, repeated at every
call site.

**Concept: scope the session, not the query.** Once a request's dependencies
have verified which org it acts in, they bind that org to the session
(`app.db.tenancy.bind_org`). A `do_orm_execute` hook then adds
`org_id = :bound` (through `with_loader_criteria`) to every ORM SELECT,
UPDATE and DELETE, on every table that has an `org_id` column. A handler
that forgets its filter gets nothing from another org: the test removes the
data-source listing's filter and the other org's source still does not
appear. A deliberate cross-org query must say `all_orgs=True`.

The full suite passed unchanged with the guard on, so no legitimate query
depended on reaching across orgs. A per-role matrix now covers assistants,
sources, connections, usage, audit and invites.

**Found on the way.**

- **Every assistant rename was a 500 on SQLite.** Nothing had tested a
  rename. After the commit, `updated_at` (set by the database) was expired,
  and reading it for the response was a lazy load outside the async context.
  Postgres returns the value with the UPDATE, which hid the bug in dev.

### 8.4 What "verified" means here

- **Live, fake driver, no spend:**
  - a turn traced into a real Langfuse v3: session, priced generation,
    tool span, `runs.trace_id` equals the Langfuse trace id;
  - `/readyz` reported degraded while MinIO was stopped, then ok again;
  - ingestion progress from 0/47 to 32/47 to ready;
  - an unchanged reindex re-embedded nothing;
  - a delegated retrieval turn kept the subagent's notes off the answer.
- **Browser:**
  - the spend meter matched the server's figures;
  - the output node's citations switch round-tripped through the compiled
    config;
  - a garbage access token plus a valid refresh token loaded the chat, and
    the page's parallel requests produced one rotation, not a revocation;
  - the subagent card nested its search;
  - the dialogs, tabs, theme toggle and toast all behaved as described.
- **Mutation-tested:**
  - every fix above, with two exceptions checked and explained:
    - the subagent `receive_response` break is equivalent;
    - the session cache's read-side repair was redundant and removed.
- **Not verified:** the Langfuse UI's rendering of a trace (signing in would
  mean typing a password into the browser). The API showed the stored tree
  instead.
