# Phase 0–3 completeness audit

*Run 2026-09-22. 109 agents: one auditor per plan task, each verdict independently
refuted by a second agent, 12 cross-cutting sweeps, one completeness critic.
6 verdicts were overturned by refutation.*

**Verdict: Phases 0–3 are not complete.** 15 of 48 tasks pass a strict reading,
32 are partial, 1 (`1.11`) is absent. More importantly, four P0 defects mean
two headline safety features — the SQL guard and the approval router — do not
hold in the configuration this project would actually ship in.

A caveat on the 32 "partial" verdicts: auditors were told a task is complete
only if *every* sub-requirement exists, so a number of them hinge on one small
missing clause. The triage below is what actually matters; the raw per-task
gap lists are in the run journal.

> **Status, 2026-09-23: all 15 P0 items are fixed** (P0-1…P0-5, F-1…F-10).
> Each was reproduced before the fix and re-checked live after it, with
> regression tests (several mutation-tested). Details and verification are in
> [EXPLAINER §6](EXPLAINER.md#6-hardening--the-phase-03-audits-p0s). Still
> open: the **"Never checked by anyone"** list and the per-task partials below.
> One P0-adjacent defect was found *during* the fixes and also fixed: Stop on
> a pending approval waited out the 5s grace period. The chat page's Stop,
> Rename and Archive buttons were verified over HTTP but still need a
> click-through in the browser.
>
> | Item | Fixed in | Evidence |
> |---|---|---|
> | P0-1…P0-3 | `sql_guard.py`, `approvals.py` | CTE-delete refused with no prompt; 81 tests, 10/10 mutations killed |
> | P0-4 | `options.py` (`tools`, `allowed_tools=[]`, `PreToolUse` gate) | real CLI: `approval_required` fires; tools offered 23 → 5 |
> | P0-5 | `db_connections.py`, `caps_mongo.py`, `pool.py` | URI sealed as `db_connection_uri`; plaintext in 0 columns |
> | F-1 | `chat.py` (`aclosing`, shielded finalize) | aborted run persisted; CLI processes 0 → 1 → 0 |
> | F-2 | `driver.py` (`UserMessage` results, `StreamEvent` tokens) | first token at 1.5s on the real CLI |
> | F-3 | `interrupts.py`, `:interrupt` route, Stop button | SDK interrupt 0.11s; Stop during approval 0.2s |
> | F-4 | `DELETE /assistants/{id}` | rows, secrets, MinIO object, Redis key gone; usage kept |
> | F-5 | archived filtered + 409 on post | live |
> | F-6 / F-10 | `graph_refs.py`, `credential_allows`, disabled toggle | errors on node; publish refused; toggle checked in browser |
> | F-7 | `Chunk.__table_args__`, `env.py` `include_object` | `alembic check` clean on SQLite (test) and Postgres (`check.ps1`) |
> | F-8 | `_authorise_role_change`, `ConversationEditorCtx` | admin takeover → 403 |
> | F-9 | `_UsageLedger`, `turn_budget()` | SDK gets the remainder; turn stops mid-stream |

---

## Corrections to earlier claims in this repo

These were written into `docs/EXPLAINER.md` and the memory file during the
Phase 3 session. The audit shows them to be wrong.

1. **§5.5 called the `exp.With` branch in `_classify` "dead code."** That was
   based on mutation testing showing `WITH … DELETE` (a CTE attached to a
   `Delete` node) is already classified correctly — which is true. But it was
   generalised to the wrong conclusion. `WITH d AS (DELETE …) SELECT * FROM d`
   is a *`Select`* carrying a mutating CTE, and it is classified `read`. The
   branch is not redundant; it is insufficient, and there is a live hole next
   to it. The EXPLAINER text needs replacing, not softening.

2. **"No endpoint can return a password"** is true of the `password` field and
   false in general. MongoDB connections are configured via
   `options: {"uri": "mongodb://user:pass@host"}` — the repo's own integration
   test does exactly this — and `options` is stored as plaintext JSONB and
   echoed back in `DbConnectionSummary`. The envelope encryption is correct;
   it is simply bypassed on that path. My "plaintext appears in zero columns"
   check only searched for the value I had passed as `password`.

3. **The Phase 3 live verification was FakeDriver-only.** That was stated but
   its significance was not. Per the SDK's own documentation, `can_use_tool`
   is *not* invoked for tools listed in `allowed_tools` — so the approval
   router does not fire on the real driver at all (see P0-4). The 30 passing
   live checks exercised `FakeDriver`, which calls `can_use_tool` by hand.

---

## P0 — security, reproduced live

I re-ran each of these against the repo's own `guard()` before recording them.

### P0-1 · A write hides inside a read *(task 3.4)*

```
WITH d AS (DELETE FROM users RETURNING *) SELECT * FROM d
WITH u AS (UPDATE users SET admin=true RETURNING *) SELECT * FROM u
SELECT * INTO newtab FROM users
```

All three return `kind=read, mutating=False`, are **allowed on a connection
with `write=False, ddl=False`**, and get a `LIMIT` cheerfully appended.
Because `mutating=False`, `classify()` returns `("auto","low")` — **no human
is ever asked.** This is the most serious finding in the audit.

### P0-2 · Set operations get no `LIMIT` *(task 3.4)*

`SELECT 1 UNION ALL SELECT 2` → `limit_applied=None`, no `LIMIT` in the
rewritten SQL. `_apply_limit` (`sql_guard.py:223`) early-returns for anything
that is not `exp.Select`, but `READ_TYPES` admits `Union`/`Except`/`Intersect`.
`docs/DATABASE_ACCESS.md:121` claims every read gets a `LIMIT`.

### P0-3 · System catalogs reachable unqualified *(task 3.4)*

`SELECT * FROM pg_roles` and `SELECT * FROM pg_stat_activity` are **allowed**;
only `pg_catalog.pg_user` is blocked. `_check_tables` (`sql_guard.py:210`)
matches the schema qualifier or a bare name equal to a listed *schema*, so
every unqualified catalog view walks through.

### P0-4 · The approval router never fires on the real driver *(tasks 3.8, agent lockdown)*

`options.py:197` passes whole-tool entries (`mcp__caps__sql_query`) in
`allowed_tools` while also setting `can_use_tool` at `:206`. The SDK is
explicit:

> `allowed_tools`: "Tool names that are auto-allowed **without prompting for
> permission**."
> `can_use_tool`: "It is **not** invoked for tool calls already permitted by
> `allowed_tools` … A `CanUseToolShadowedWarning` is emitted … if this callback
> is set alongside `allowed_tools` entries that allow a whole tool."

So with `AGENT_DRIVER=claude`, every Phase 3 approval is skipped and the
deny-by-default gate promised in ADR 0003 does not exist. The SDK names the
fix: use a `PreToolUse` hook to gate *every* call regardless of permission
rules.

### P0-5 · Mongo bypasses both the envelope and the permission profile *(tasks 3.1, 3.2, 3.6, 3.7)*

- Credentials in `options["uri"]` are stored plaintext and returned by the API.
- `caps_mongo.py:111-159` never checks `perms.read`, `allow_tables` or
  `deny_tables` — a `read:false` connection still returns documents and a
  denied collection is readable by naming it.
- `limit=0` clamps to 0, which pymongo treats as *unlimited*.
- `pool.py:54-68` omits `options` from the pool key while `:184` builds the
  connection string from it, so two connections differing only in credentials
  share one authenticated client.

---

## P0 — functional gaps

| # | Gap | Task |
|---|---|---|
| F-1 | **A disconnect loses the whole turn.** `conversations.py:82` breaks the generator; `chat.py:212-291` has no `try/finally`, so the assistant message, `Run` row, `UsageEvent` and cost rollup are all skipped. `GeneratorExit` is not `CancelledError`, so `status=aborted` never runs either. Spend is unbilled and the turn leaves no record. | 1.8 / 1.10 |
| F-2 | **The real driver never emits `tool_result`.** The SDK delivers `ToolResultBlock` inside `UserMessage`; `driver.py:186-204` only inspects `AssistantMessage`. Masked entirely by `FakeDriver`. | 1.5 / 3.9 |
| F-3 | **Interrupt does not exist on either side.** No route, no `client.interrupt()`, no `AbortController`; `streamMessage`'s `signal` param is never passed by its only caller. | 1.6 |
| F-4 | **`DELETE /assistants/{id}` does not exist** — no route, service or client call. So "delete-assistant cascades" is unimplemented and orphaned `Secret` rows and MinIO objects have no teardown path. | 1.2 |
| F-5 | **Conversation delete is a no-op.** `chat.py:102` sets `status=archived`; `list_conversations` has no status filter, so it reappears on reload and still accepts messages. | 1.7 |
| F-6 | **`expose_write` is not gated on the connection's `write` permission**, which is literally what the task row asks for. `panels.tsx:288` only dims the toggle; there is no server-side gate. | 3.11 |
| F-7 | **`alembic check` fails today.** The HNSW and GIN indexes are raw `op.execute` and undeclared on the model, with no `include_object` filter — so the next autogenerate emits `drop_index` for both. (This is the trap already hit once this phase; nothing prevents a repeat.) | 2.1 |
| F-8 | **Privilege escalation:** any admin can `PATCH` their own membership to `owner` then demote the founding owner — no actor-rank check. `RequireOwner` is defined and applied to **zero** routes. Any org member can rename or archive another member's conversation, with no audit entry. | 0.7 |
| F-9 | **Budget enforcement is post-hoc.** `cost_usd` only arrives in the terminal usage event, so the check fires after the answer has streamed and the money is spent. `options.py:171` also hands the SDK the full cap every turn rather than the remainder. | 1.8 |
| F-10 | **Referenced-id ownership is validated nowhere.** `connection_id` / `data_source_id` are never checked to belong to the same assistant+org — not even a format check, so a non-UUID publishes cleanly and raises `ValueError` at turn start. | 1.1 / 1.12 |

---

## Never checked by anyone until the critic looked

> **Status, 2026-09-24: all eight fixed, plus the canvas node-id defect from
> the note at the end of this doc.** Details in
> [EXPLAINER §7](EXPLAINER.md#7-the-never-checked-by-anyone-list).
>
> | Item | Fixed by |
> |---|---|
> | Pagination | `db/pagination.py` keyset pages on every list; the detail inlines only the latest 50 messages |
> | OpenAPI types | `openapi.json` + `api.gen.ts` in `packages/shared`; pytest snapshot + `gen:check`; web types are aliases |
> | Version history / diff UI | History tab on the build page; publish asks for a note |
> | `runs.trace_id` | column + migration `c5a1f7e2d904`; bound into the turn's logs |
> | `runs` write-only | `GET …/runs`, `…/runs/{id}`; "Run details" in chat |
> | Invite accept UI | `/invites/[token]` page, Members page, token preview endpoint |
> | `packages/shared` dead | holds the generated types; typechecked and stale-checked in CI and `check.ps1` |
> | CI images / audits | `docker` job builds and smoke-starts both images; audits fail the build |
>
> Found while fixing them: SQLite keyset pages looped forever (timestamp
> text formats); `?next=` lost between login and register; a global CSS rule
> that stopped every `border-*` colour class from applying; three untyped or
> wrongly-optional response schemas; a vulnerable nested `postcss`;
> `.env` inline comments that broke `docker run`; 420 MB of unused Node in
> the API image. Still open: no Python lockfile, so image builds are not
> reproducible.

- **No list endpoint is paginated** except the audit log, though §8 says "all
  list endpoints paginated". The messages array inlined into
  `GET /conversations/{id}` is unbounded too.
- **No OpenAPI type generation.** §8 and §11.1 require TS types emitted to
  `packages/shared` and a schema snapshot test. `apps/web/lib/api.ts` is 531
  hand-written lines that can silently drift from the Pydantic schemas — the
  root cause of several drift bugs found piecemeal elsewhere.
- **No version-history or diff UI.** `assistants.versions()` has zero callers;
  the diff endpoint built in 1.2 is not even in the client.
- **`runs.trace_id` does not exist** — the column §2.5 specifies was never
  added, so spans have nothing to correlate against.
- **The `runs` table is write-only.** Nothing ever reads a `Run` row.
- **No invite-accept UI**, though both endpoints exist.
- **`packages/shared` is dead** — never installed, linted, or type-checked by
  any runner; a regression there passes CI silently.
- **CI never builds the Docker images**, and both `pip-audit` and `npm audit`
  are `|| true`, so a critical advisory never reds the build.

---

## Per-task verdicts

| Phase | Complete | Partial | Missing |
|---|---|---|---|
| 0 | 0.2, 0.5, 0.6, 0.9 | 0.1, 0.3, 0.4, 0.7, 0.8 | — |
| 1 | 1.13, 1.15 | 1.1–1.10, 1.12, 1.14 | **1.11** |
| 2 | 2.1, 2.2, 2.4, 2.7, 2.9, 2.11, 2.12, 2.13 | 2.3, 2.5, 2.6, 2.8, 2.10 | — |
| 3 | 3.5 | 3.1, 3.2, 3.3, 3.4, 3.6–3.11 | — |

Cross-cutting sweeps: `secrets-hygiene`, `agent-lockdown`, `sql-guard-tests`
and `data-lifecycle` came back **high** severity; the other eight medium.

### Status after the fixes (2026-09-24)

The table above is the audit as it was. Since then every partial and missing
item in it has been closed, each with tests (mutation-checked) and, where it
has a live surface, verified on the running stack. Where each one landed:

| Area | Where it is written up |
|---|---|
| P0 findings | EXPLAINER §6 |
| "Never checked by anyone" list | EXPLAINER §7 |
| Phase 3 partials (3.1–3.11) | EXPLAINER §5, `docs/DATABASE_ACCESS.md` |
| Phase 1 partials, 1.11 | EXPLAINER §8.1 |
| Phase 2 partials | EXPLAINER §8.2 |
| Phase 0 partials (0.1, 0.3, 0.4, 0.7; 0.8 in §7) | EXPLAINER §8.3 |

Two audit bullets were already fixed when the gap work reached them (the
admin-to-owner escalation and conversation mutations, EXPLAINER §6); their
notes above predate that.

---

## What is genuinely solid

Spot-checked by the critic against the source, not taken on trust:

- **2.9 citations** — conversation-scoped marker registry, code-fence masking,
  a run-anchored regex that correctly rejects `items[1]`, spans into the
  persisted string. Described as "genuinely careful work".
- **2.11 evals**, **2.12 data-sources UI**, **2.4 embedders/rerankers**,
  **2.2 data-source CRUD**, **0.6 auth** — all confirmed.
- **2.1 pgvector/HNSW + FTS**, **2.7 hybrid retrieval + RRF**, **2.13 canvas
  KB nodes** — confirmed.
- Repo gates are green and were re-run independently: `ruff` clean, `mypy`
  clean across 133 files, 307 unit tests pass.

The envelope encryption itself is correct — `seal`/`open_sealed`/`rewrap`, AAD
binding, redacting repr. The problem is only that the Mongo URI path never
reaches it.

---

## Suggested order

1. **P0-1 … P0-3** — `sql_guard` classification, `LIMIT` on set ops, catalog
   matching. Smallest diffs, largest risk reduction. Add the missing cases from
   §10's checklist as tests first; they fail today.
2. **P0-4** — move gating to a `PreToolUse` hook, or drop whole-tool entries
   from `allowed_tools`. Until this lands, nothing about approvals is true with
   a real key.
3. **F-1** — `try/finally` around the turn; cheap, and it is losing billing data.
4. **P0-5** — route Mongo credentials through the envelope; enforce the
   permission profile in `caps_mongo`.
5. **F-8** — actor-rank check on role changes; apply `RequireOwner`.
6. Then the functional gaps (F-3 … F-7, F-9, F-10) and the never-checked list.

A note on process, from the critic: two auditors each marked their own task
complete while handing the *same* reproduced canvas-node-id defect
(`build/page.tsx` mints `ds`/`db`, `project.py:116` re-derives `src:{id}`/`db:{id}`,
so the first Panels save relocates and renames the node) to the other. A
confirmed defect should have to land on some task's ledger before either can
close.
