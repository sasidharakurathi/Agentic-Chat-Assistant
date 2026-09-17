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
a **local Postgres on `:5432`**, so the compose stack publishes on **`:55432`**
(and Redis on `:56379`) to avoid the collision.

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

## 3. Phase 1 — Config schema + graph model  *(in progress)*

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
