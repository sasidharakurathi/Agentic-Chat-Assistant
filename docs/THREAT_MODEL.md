# Assistant Studio threat model

What the platform protects, from whom, where the walls are, and what is
still open. Written during the Phase 6 security pass (task 6.3,
2026-09-30), which reviewed each wall by trying to get through it.
`docs/EXPLAINER.md` §12.3 tells the story of that pass; this document is
the reference.

## 1. What is being protected

| Asset                            | Where it lives                                           | Worst case if lost                                  |
| -------------------------------- | -------------------------------------------------------- | --------------------------------------------------- |
| A customer's own databases       | reached through stored connections                       | data read, changed or destroyed by a model          |
| Database and MCP credentials     | `secrets` table, sealed with the KEK                     | direct access to customer systems                   |
| The KEK, JWT secret, vendor keys | the server's environment                                 | every sealed secret, every session                  |
| Each org's content               | assistants, documents, chunks, conversations, memory     | one tenant reading another's                        |
| Accounts                         | `users`, refresh tokens                                  | takeover                                            |
| The server and its network       | the API, worker and runner containers                    | the platform used to reach internal services        |
| Money                            | model and embedding spend                                | a bill run up by someone else                       |

## 2. Who might attack

1. **Someone with no account.** Reaches the sign-in routes and an invite
   link if they have one.
2. **A signed-in user of another org.** Registration is open and every
   user gets a personal org, so this is anyone. Knows or guesses ids.
3. **A member of the same org** with the lowest role.
4. **An assistant's end user**, talking to a published assistant.
5. **The model itself**, steered by a prompt injection in a user message,
   a document, a web page, a database row or an MCP tool's output. It can
   call only the tools it was given, with any arguments.
6. **A third-party MCP server**: its tool names, descriptions, schemas and
   results are all untrusted input, and a local one is untrusted code.
7. **Someone who can read the logs or traces** but not the database.

Out of scope: an operator with shell access to the server, or anyone who
holds the KEK and the database together.

## 3. The walls

### 3.1 Between orgs

- Every route establishes who is calling and for which org before it
  loads anything, and answers "not found" (not "forbidden") for another
  org's object, so an id confirms nothing (`api/deps.py`).
- A request's database session is bound to its org; every ORM query gets
  the org filter added (`db/tenancy.py`). The explicit membership check in
  each dependency is what protects a lookup by id; the filter is the net
  under it.
- Paths with a session of their own (the chat stream, tool handlers,
  worker jobs, the eval runner) scope every query by the assistant or
  source id the server already authorised.
- Ids inside a saved config (a connection, a source, an MCP server) are
  re-scoped to the assistant at run time: another org's id resolves to
  nothing.

**Tested by** `tests/test_tenant_isolation.py`, which walks every route the
API publishes (from the OpenAPI schema, so new routes are covered) as a
user of another org, with the victim's ids and with its own parent and the
victim's child. `tests/test_tenancy.py` tests the session filter.

### 3.2 Inside an org

| Action                                                       | Who                                      |
| ------------------------------------------------------------ | ---------------------------------------- |
| Read assistants, conversations, runs, usage                  | any member                               |
| Create an assistant; edit one they created                   | any member                               |
| Edit any assistant; budgets; invites; roles; audit log       | admin, owner                             |
| Invite or promote to a role                                  | only up to the actor's own role          |
| Post into, rename, stop or archive a conversation            | its creator only (admins read it)        |
| Approve or decline a pending tool call                       | the conversation's creator, or an admin  |
| Start a conversation as an end user; read end-user memory    | the assistant's editors                  |
| Add, change or start a **local-command** MCP server          | admin, owner                             |

**Deny by default (Phase 7a.3).** Every route that acts inside an org finds
the caller's membership through one function (`app/security/access.py`),
which also applies the Studio floor: the lowest role that may use the
Studio at all, `member` today. A role added below it later (the Employee
role in Phase 8) is refused on all of those routes at once, instead of
passing wherever a route only checked membership. The full table, one row
per route, is `ROLE_MATRIX` in `tests/test_role_matrix.py`; the test fails
on a route the table doesn't list, on any refusal or access the table gets
wrong, and on any org route a role below the floor can reach. The only
routes outside the floor are sign-in, the public schemas, and the
caller's own profile, org list, org creation and invite acceptance.

### 3.3 Between the model and a customer's database

The model writes SQL. Before anything runs (`datasources/sql_guard.py`):

1. exactly one statement, parsed; anything unparseable is refused;
2. classified read, write or schema change from the parse tree, with any
   mutation nested inside a read refused;
3. the connection's profile applied: read/write/DDL flags, allowed and
   denied tables (system catalogs always denied), a row limit forced onto
   reads, a statement timeout;
4. functions that write, reach files or the network, inspect the server
   or dump tables by name are refused. Whole families (`pg_*`, `lo_*`,
   `dblink*`, `*_to_xml`) are refused by prefix, because a list of names
   always loses to the next sibling;
5. a write goes to a person, who sees the exact statement; what runs is
   the pinned copy they saw.

The credential should still be a least-privilege role
(`docs/DATABASE_ACCESS.md`): the guard is the second wall, the database's
own permissions are the first.

### 3.4 Between the server and the network (SSRF)

Every request to an address a builder or the model chose goes through
`security/ssrf.py` (`http_request`, URL sources) or the pinned transport
(remote MCP servers):

- http(s) only; no credentials in the URL; for the `http_request` tool,
  only the assistant's allowed domains, and with none listed it reaches
  nothing (Phase 7a.9; a list that says nothing usable allows nothing). A
  URL source is fetched from the address its builder typed;
- the name is resolved once, **every** address it has must be public, and
  the connection goes to that checked address (no second lookup to
  rebind);
- IPv6 forms that carry an internal IPv4 (mapped, 6to4, Teredo, NAT64,
  IPv4-compatible) are unwrapped first;
- redirects are followed by hand and re-checked at each hop; never from
  https down to http; another origin gets none of the caller's headers
  and no replayed body;
- size and time limits; a compressed body is never inflated; a page is
  only decoded with a real text encoding;
- one message for "no such name" and "an internal name".

**Data out through allowed reads.** A GET runs without asking anyone,
and a URL can carry data: a poisoned document can have the model read rows
and then put them in the query string of a request. The allowlist is what
bounds this, which is why an empty one now means no sites. It only helps
if the allowed sites are ones nobody else controls: the builder is warned
about sites where anyone can publish or collect content (a paste, a gist,
a form, a request catcher), since allowing one of those lets data out
anyway. Web search can carry data in its query the same way; that is
planned with Phase 9 (9.4).

Not covered, by design: **database connections**. A builder gives a host
and port and the server connects. Customers' databases are usually on a
private network, so this cannot be limited to public addresses; it means
a builder can probe and connect to hosts the server can reach. Restrict
who can sign up (see §5) if that matters for your deployment.

### 3.5 Around MCP servers

- **Remote** servers: https only, pinned, no redirects, auth headers
  sealed and never returned.
- **Local-command** servers never run in the API process. The runner
  starts them in a jail: a private directory, a scrubbed environment (none
  of the platform's secrets), resource limits with ceilings below the
  container's, no new privileges, and in Docker no network and a
  read-only filesystem. The jail refuses to start a command if it cannot
  lock itself down.
- Tools: only allowlisted tools of enabled servers are offered; the
  allowlist is enforced again at call time; a server cannot name a tool
  to look like a platform tool; results are size-capped, stripped of
  secrets and scanned for injection before the model sees them.
- Approval: by default every MCP tool call asks a person.

### 3.6 Around secrets

- Sealed at rest with a per-secret key wrapped by the KEK (AES-GCM); never
  in a response model, the audit log, the model's context or a tool
  description.
- Logs: credential-named fields are replaced, credential-shaped text is
  masked (by vendor format, and by position: `password=`, `"token": "…"`,
  an `Authorization:` line, a connection URI), and tracebacks are rendered
  to text and scrubbed like everything else, without local variables.
- The same masking runs on tool results before the model sees them, on
  stored tool inputs and outputs, on approval cards and on trace spans.
- The access log records the route (`/invites/{token}`), not the address.
- The agent's CLI subprocess gets the server's environment with the
  server's own secrets blanked.
- Production refuses to start with a placeholder or short JWT secret, no
  KEK, no runner token, the default object-store password, or the
  local-only MCP flag.

### 3.7 Around spend and abuse

Budgets per org and assistant, a cap per conversation, rate limits per
address, per user and per org (tighter on sign-in, chat, the AI helpers
and anything that starts a job), bounded turn concurrency, and size caps
on suites, uploads and tool output.

## 4. What the pass found and fixed

Five reviewers each took one surface and tried to break it; everything
below was confirmed by running it before it was changed.

| Finding                                                                                          | Severity | Now                                                                  |
| ------------------------------------------------------------------------------------------------ | -------- | -------------------------------------------------------------------- |
| A SQLite "connection" could point at the platform's own database file (every password hash)      | critical | confined to `DB_SQLITE_DIR`; the platform's file always refused      |
| `pg_catalog."pg_read_file"(…)`: a quoted, qualified function name passed the banned list         | critical | names resolved through the identifier                                |
| An admin could invite an owner, accept it themselves and demote the founder                      | high     | an invite grants at most the sender's role                           |
| Function siblings not on the list (`lowrite`, `pg_stat_reset`, `dblink_send_query`, …)           | high     | families refused by prefix                                           |
| `table_to_xml('secret')` dumped a denied table                                                   | high     | `*_to_xml` refused                                                   |
| Console logs printed tracebacks with local variables, unredacted; JSON logs had none             | high     | rendered to text, then scrubbed; no locals                           |
| `pyjwt` 2.13 (ten CVEs)                                                                          | high     | 2.14+                                                                |
| Any member could approve a teammate's pending write                                              | medium   | the conversation's creator or an admin                               |
| A member could start a conversation as any end user and read their memory                        | medium   | editors only                                                         |
| Any member could register and start a local command in the runner                                | medium   | admins and owners                                                    |
| NAT64 and other IPv6 forms of internal addresses passed the SSRF check                           | medium   | unwrapped                                                            |
| A small gzip response inflated past the size cap                                                 | medium   | compressed bodies are not read                                       |
| `charset=punycode` on a page stalled the worker for minutes                                      | medium   | known text encodings only, off the event loop                        |
| A schema-qualified deny entry missed the bare table name; a bare allow entry admitted any schema | medium   | matched by schema, deny loosely and allow strictly                   |
| Many credential shapes passed redaction (`redis://:pw@`, `?password=`, `x-api-key:`, JSON)       | medium   | recognised by position as well as shape                              |
| Raw exception text reached the browser over the chat stream                                      | medium   | a fixed message                                                      |
| No security headers on the API or the web app                                                    | medium   | `nosniff`, no framing, no referrer                                   |
| Invite tokens in the access log                                                                  | low      | the route template is logged                                         |
| Login answered faster for unknown addresses; a refresh token could be used twice in a race       | low      | a decoy hash; claimed in one conditional update                      |
| An allowlist of only blank entries allowed everything                                            | low      | allows nothing                                                       |
| Credentials crossed a redirect to another port or down to http                                   | low      | dropped across origins; downgrade refused                            |
| Runner: limits above the container's, `LD_PRELOAD` accepted, stderr could echo an env secret     | low      | lower ceilings, loader variables refused, env values removed by value |

**Phase 7a.1: scripts in uploaded files (found by the gap review).** A
knowledge file was stored with the browser's content type and served from
the app's own origin, so an HTML upload with a script, opened from a
citation by an admin, ran as the app and could read the sign-in tokens.
Fixed with three walls: the server decides the type at upload from the
bytes and refuses anything outside PDF, Word, HTML, Markdown and text; each
link signs the type and disposition the server chose (text formats as
plain text, PDF inline, Word as a download, anything else as opaque
bytes), so stored objects are never trusted; and Caddy adds `nosniff` and
a no-scripts policy on the files path (`storage/file_types.py`,
`tests/test_file_types.py`).

**Phase 7a.2: data sent out through images in answers (found by the gap
review).** A poisoned document or web page could steer the model into
writing a Markdown image whose address carried conversation data, and the
browser fetched it the moment the answer rendered, with no click (the
EchoLeak pattern). Fixed with two walls: images in model output are never
loaded (a note names what was left out and its host), and the app's
content policy allows images only from itself (`img-src 'self' data:
blob:`). Links to other sites now show their host beside the text, so a
link labelled "Refund policy" can't hide where it goes (`lib/safe-links.ts`,
`components/chat/markdown-safety.test.ts`, `security-headers.test.ts`).

**Phase 7a.3: a new role would have been let in everywhere (found by the
gap review).** Of the 79 routes that act inside an org, only 5 asked for a
role; the rest checked that the caller belonged to the org, or had created
the assistant or conversation. A role below `member`, such as the planned
Employee role, would have passed those 74: drafts, runs and
traces, evals, memories, the AI helpers, and creating an assistant (which
makes its creator an editor, free to add databases and MCP servers).
Fixed by the Studio floor described in section 3.2, applied in the shared
dependencies, the eval-suite context and the approval decision; the MCP
catalog and runner status, which only asked for a signed-in user, now
need an org membership too.

**Phase 7a.4: an admin could act in an employee's name (found by the gap
review).** Admins could post into anyone's conversation. A message runs the
agent as the conversation's owner (their memory today, their identity for
"my tickets" lookups and requests once Phase 9 binds it), so an admin's
message would act in that person's name, and the admin could then approve
the write it caused. Posting, stopping, renaming and archiving are now the
starter's alone; admins keep read access (plan D5). Approval decisions are
unchanged: the starter or an admin, until admin-approved actions get their
own executor (plan D17). The chat page shows anyone else a read-only view.

**Phase 7a.5: full transcripts outside every deletion path (found by the
gap review).** The Claude CLI writes each session to a JSONL file (prompts,
tool output including SQL rows, answers) in the server user's home. Nothing
removed them, not even deleting the assistant, so a deleted conversation
stayed on disk in full for anyone with a shell in the container (in
development, in the developer's own `~/.claude`). They now live in one
known place (`AGENT_STATE_DIR`, a named volume) and go when their
conversation is archived or its assistant deleted, with a daily sweep for
anything left over (`agent/cli_files.py`). They stay unredacted while the
conversation lives: whoever can read the volume can read them.

**Phase 7a.6: an unpublished object store, floating versions, and spend
the CLI mispriced (found by the gap review and the real-model check).**
`minio/minio` is no longer published and its code is archived, with two
signature-bypass CVEs in older releases. The stack now runs Chainguard's
source-built MinIO, pinned by digest and past those fixes; the proxy
never let writes through to it anyway. Every Python dependency is pinned
exactly (`apps/api/constraints.txt`), so a rebuild cannot pick up a new
Claude CLI or a new SQL classifier unseen. The CLI, which priced models it
didn't know at Opus 5's rates, no longer sets what a turn costs: the
platform prices each turn from its own table, so budgets hold on new
models. In development the CLI was signing in with the developer's own
Claude login instead of the platform's key; it is now given the key.

**Phase 7a.7: approval records that could say what didn't happen (found by
the gap review).** A decision arriving just after a turn stopped waiting
was recorded as "approved" although nothing ran, two simultaneous decisions
could both pass, and a process killed mid-wait left its approval pending
forever. A decision is now one conditional update (still pending, not past
its expiry) with its audit entry in the same transaction; the waiting turn
acts on what the row says, and marks an approval it could not act on
`cancelled`; and a sweep every minute closes orphaned approvals with a
system audit entry and tells the asker when a crash left their question
unanswered.

**Phase 7a.8: sessions lost to a bad connection, and logs that vanished
(found by the gap review).** A lost refresh reply read as token theft and
revoked the session, and the app signed people out on any network error.
A retry within 30 s of a token's use, in a session that hasn't moved on,
now gets a fresh pair (the unused one from the lost reply is revoked);
other reuse still revokes the session. Refreshes are limited per session
rather than per address. For investigations, the proxy keeps a 183-day
access log with credentials cut from addresses, the API's access line
names the user and address, and the audit log is append-only in Postgres.

**Phase 7a.9: the HTTP tool could reach any site (found by the gap
review).** With no allowlist it reached any public host, and a GET needs
no approval, so one poisoned page could send data out in a URL with
nobody asked. An empty list now reaches nothing and says so to the model
and the builder, and allowing a site where anyone can publish draws a
warning. The approval gate's matching rule (a version with this tool and
no list can't be approved) comes with Phase 7b.

## 5. What is still open

These are known, and either accepted for a self-hosted, single-operator
deployment or waiting for a decision. They are the honest edge of what
the platform defends.

1. **The MCP runner is one trust zone.** Every local-command server runs
   as the same user in the same container, sharing `/tmp` and `/proc`. A
   malicious server can read another server's environment (its decrypted
   secrets) and the runner's token. Until each session gets its own user
   or container, treat the runner as single-tenant: only admins can add
   commands (fixed in this pass), and they should add only servers they
   trust. Do not give the runner egress (`docker-compose.mcp-egress.yml`)
   on a server shared between orgs that don't trust each other.
2. **A server's own "read-only" hint is trusted** when a builder sets an
   MCP server's approval to automatic. The default is to ask; keep it for
   servers you don't control.
3. **Re-discovering an MCP server changes what published versions offer**
   (its descriptions and schemas are read live). A server can change a
   tool's description after it was reviewed.
4. **Open registration, by default in development.** With
   `REGISTRATION=open` anyone who can reach the server can create an
   account and an org, and then use the builder: register database hosts
   to connect to, URL sources to fetch, remote MCP servers, all on the
   server's model key. `REGISTRATION=invite` (task 6.6; the default in
   `docker-compose.prod.yml`) allows the first account and then only the
   holder of a live invite link for their own address. There is still no
   email verification.
5. **Any signed-in person can create an org** (`POST /orgs`), and so be
   its owner with full Studio access there. It is outside the Studio floor
   by design today; when the Employee role arrives (Phase 8), org creation
   must be limited, along with the personal org made at registration.
   **Members cannot be removed and invites cannot be revoked** (there is
   no route). A departed member can only be lowered to `member`, which
   still reads the org's assistants and conversations.
6. **Sign-in tokens live in the browser's local storage.** There is no
   script-source policy on the app (Next would need per-request nonces), so
   an XSS bug could read them. Two surfaces are now handled: model output
   is rendered as Markdown without raw HTML, and links can't be
   `javascript:`; and uploaded files, which the first pass missed, are
   typed and served by the server (Phase 7a.1, see section 4). Moving
   tokens out of local storage remains open.
7. **Logging out does not end the 15-minute access token**, only the
   refresh token.
8. **A sealed secret is bound to its kind, not its owner.** Someone who
   can write to the database (but has no KEK) could attach another org's
   sealed password to a connection they control.
9. **Personal-data redaction is narrow**: it covers web-search queries and
   trace spans, and a limited set of formats.
   **A steered model can still put data into a web-search query**, which
   goes to the search provider. Images and links in answers are closed
   (Phase 7a.2, section 4); a per-assistant policy for web search on
   assistants that read restricted data is planned with Phase 9 (9.4).
10. **No size cap on what a remote MCP server sends** before it is parsed.
11. **`vitest` has two moderate advisories** (dev tooling only; the fix is
    a breaking major upgrade).
12. **MySQL optimizer hints** in a read are passed through (bounded by the
    adapter's own timeout), and a `CREATE FUNCTION` body is not checked
    against the table lists (it needs schema-change permission and a
    person's approval).

## 6. For operators

- Set `APP_ENV=production`; it turns on the start-up checks in §3.6.
- Give every database connection a least-privilege role
  (`docs/DATABASE_ACCESS.md`).
- Leave `DB_SQLITE_DIR` empty unless you need SQLite connections; if you
  do, point it at a folder that holds only those files.
- Keep Postgres, Redis and MinIO off public interfaces. Redis carries
  approval decisions and the job queue. The production compose file
  publishes none of them and gives Redis a password; the development one
  publishes all three with default credentials.
- Set `REGISTRATION=invite` on a server people can reach from outside
  (the production compose file does).
- Keep `MCP_ALLOW_INSECURE_URLS` off (production refuses it).
- Run `pip-audit` and `npm audit` on upgrades; CI fails on either.
