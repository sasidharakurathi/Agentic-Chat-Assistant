# Assistant Studio threat model

What the platform protects, from whom, where the walls are, and what is
still open. First written in the Phase 6 security pass (task 6.3,
2026-09-30) for a server many organisations could sign up to; rewritten
for internal use in Phase 7b.10 (2026-10-09): one company, its builders
and admins now, every employee once Chat opens (Phase 8). `docs/EXPLAINER.md`
§12.3 and §17 tell how each wall was built; this document is the
reference.

## 1. The deployment this assumes

- **One company, one organisation** (`ORG_MODE=single`, the production
  default since 7b.8). People join by invite; nobody creates a second
  organisation.
- **Reached on the office network and over Tailscale** (plan D15, Phase
  8.2): the certificate comes through DNS, the proxy listens on one
  private address, and the name resolves to it only inside (OPERATIONS
  §2). A server set up with `TLS_MODE=public` is reachable from the
  internet instead, and the preflight says so.
- **Today's users are builders and admins.** Everyone invited is at least
  a `member`, which is a builder with the whole Studio. Employees who only
  chat arrive with the Employee role (8.1); section 7 lists what must hold
  before they do.
- **The model is Anthropic's, through its API**; documents are embedded by
  Voyage or by the local model (plan D18). What leaves the building is
  listed in section 6.
- **One operator** runs the server, holds the KEK and the backups.

## 2. What is being protected

| Asset | Where it lives | Worst case if lost |
| --- | --- | --- |
| Employees' conversations | messages, runs, tool calls; the CLI's transcripts (`AGENT_STATE_DIR`); backups; traces if Langfuse is on | private questions read by colleagues, or outside the company |
| The company's databases | reached through stored connections | data read, changed or destroyed by a model |
| Knowledge documents | MinIO, chunks in Postgres | internal documents read by the wrong people |
| Database and MCP credentials | `secrets`, sealed with the KEK | direct access to company systems |
| The KEK, JWT secret, model and SMTP keys | the server's environment | every sealed secret, every session, the company mailbox |
| The DNS credentials and the certificate's key | the proxy's environment; the `caddydata` volume | with acme-dns, one TXT record; with a wider token, the zone: someone could issue a certificate for the name |
| Accounts and sessions | `users`, refresh tokens, one-time links | someone acting as an admin |
| What answers in Chat | approved versions, their manifests | a builder's untested change reaching everyone |
| The record of who did what | the audit log and its hash chain; the proxy's access log | an insider covering their tracks |
| The server and its network | the API, worker, mailer and runner containers | the platform used to reach internal services |
| Money | model, embedding and search spend | a bill run up by mistake or on purpose |

## 3. Who might attack

1. **Someone on the network with no account.** Reaches the sign-in,
   recovery and invite routes. Today, possibly from the internet (section
   1).
2. **A member (builder).** Builds assistants and so chooses database
   hosts, URL sources, remote MCP servers and sites the HTTP tool may
   reach. Should not read colleagues' conversations, approve their own
   work, or change what Chat answers without review.
3. **An admin, as an insider.** May read any conversation (plan D5) and
   approve versions and writes. Should leave a trace each time, and should
   not act in an employee's name or approve their own work.
4. **The owner.** Trusted with the organisation; the export and the
   integrity check are theirs. Their choices (switches, self-approval)
   are recorded.
5. **A leaver**, removed but still holding a session, an invite they sent,
   or knowledge of credentials.
6. **The model itself**, steered by a prompt injection in a message, a
   document anyone in the company can edit, a web page, a database row or
   an MCP tool's output. It can call only the tools it was given, with
   any arguments.
7. **A third-party MCP server**: its tool names, descriptions, schemas and
   results are untrusted input, and a local one is untrusted code.
8. **A forged or stolen email link**: invites, reset links and review
   notices are what a phishing message would imitate.
9. **Someone with the logs, traces or backups** but not the application.
10. **Someone with the database** but not the KEK: they can change rows
    directly. Out of scope, except that the audit chain makes their
    changes evident (4.12).

11. **A Google account that isn't the person's**: a personal account with
    the company address, an account of another domain, someone who
    registered the address first with a password, a recycled mailbox
    (Phase 8.3).

Planned, and part of the second security pass (section 7): shared links
and audiences.

Out of scope: the operator with shell access, or anyone holding the KEK
and the database together.

## 4. The walls

### 4.1 Who may do what

| Action | Who |
| --- | --- |
| Chat with an assistant (its approved version only) | its audience (8.5), employees included; admins and its builders always |
| Anything in the Studio | not an employee: refused at the Studio floor |
| Read a conversation, its runs and approvals | its starter; an admin or the owner with a recorded reason (4.2) |
| Post into, rename, stop or archive a conversation | its starter only (admins read it) |
| Create an assistant; edit, preview and submit one they may edit | any member; editing is its creator, its co-editors, admins and the owner |
| Approve a version | admin, owner; not its submitter or builder (the sole owner may, with a note, unless switched off) |
| Reject a version; roll back; pause and reopen | admin, owner |
| Approve or decline a pending tool call | an admin; the conversation's starter too, unless an employee (plan D7, 8.1; D17 until Phase 9) |
| Invite, or change a role | admin, owner; only up to the actor's own role |
| Remove or reactivate a member; withdraw an invite | admin, owner; an admin only below their own rank |
| Budgets; mail status; the audit log | admin, owner |
| Name an assistant's co-editors | its creator, admin, owner |
| Add, change or start a **local-command** MCP server | admin, owner |
| Rename the organisation, change its switches; export the audit log; check its integrity | the owner |

**Deny by default (Phase 7a.3).** Every route that acts inside the
organisation finds the caller's membership through one function
(`app/security/access.py`), which refuses a removed member, an archived
organisation and any role below the Studio floor (`member` today). A role
added below it, the Employee role in Phase 8, is refused everywhere at
once until a route is opened to it on purpose. `ROLE_MATRIX` in
`tests/test_role_matrix.py` holds one row per route; the test walks every
route the API publishes as each role and fails on a route the table
doesn't list or a result it gets wrong. The appendix lists those rows in
words, and a test fails when it differs from the matrix.

**What an employee receives (Phase 8.1).** The server leaves the working
parts of an answer out of every response and streamed event to an
employee (`services/chat_view.py`): a tool's input and output, the model,
tokens, cost, the model's thinking, budget warnings, the statement of
their own request waiting. It is defence in depth: an employee can ask
the model to repeat what a tool said, so what a tool returns to whom is
the tool's to decide (D8, Phase 9.3).

### 4.2 Private conversations, and the admins who read them

Each person lists and reads only their own conversations (plan D5,
Phase 7b.2). An admin or the owner may open anyone's by giving a reason:
one check on every conversation read records the reader, the
conversation and the reason in the audit log, and commits it before
anything is returned. Other people's titles are left out of lists and
Usage, and the audit log stores no titles or content. Admins can't post
into another person's conversation, stop it or approve what it caused in
their name (7a.4). The owner sees every read on the Audit log page
(**Admins opening conversations**), and since Phase 8.7 the person read
sees each one too (**Privacy & help › Access to your conversations**),
with the reason. A conversation its owner removed from their history
(Phase 8.8) is kept and still readable by admins, and such a read is
flagged `hidden_by_owner`; the person is told this when they remove it.
Feedback comments reach the assistant's builders with only their question
and answer, never the asker's name; only admins see the conversation they
came from. Before their first message everyone accepts a notice
that says admins can read conversations; the server refuses the message
otherwise (`notice_required`). Reads made outside the app (the database,
backups) are in neither.

**Erasing** (Phase 8.10) is one service that reaches the rows, memory
notes, CLI transcripts and session keys, skips legal holds, and records
what it erased (ids only) so a restore can be followed by erasing it
again. Traces carry no content in production unless `TRACE_CONTENT=full`,
which preflight flags. Backups keep erased data until they age out (14
days).

### 4.2a Knowledge groups and shared links (Phase 9.4)

- **Each source is everyone's or readable by groups**, deny by default.
  A deleted group's id matches no one, so deleting a group never opens a
  source.
- **Retrieval and citations** use only what the asker may read: the turn's
  config is narrowed before any tool is built. An assistant is offered
  only to people who can read all of its restricted sources.
- **Losing access bumps an access epoch.** A conversation on restricted
  knowledge then starts with no resumed session, summary, replay or
  carried citations. The memory tool is off there.
- **Not covered:** the text of an earlier answer stays visible to the
  person it was given to; only its citations hide.
- **A source open to everyone is scanned before indexing.** One
  credential, or three or more Aadhaar, PAN or card numbers, holds it
  until its builder chooses groups or confirms (audited with the counts).
  Pattern-based, so names and salaries pass.
- **Share links** hold only a token hash. Each opening re-checks four
  things: the link isn't revoked, the sharer is still a member, the reader
  may use the assistant, and the reader may read every cited source. A
  refusal says no more than it must. The copy shows messages and
  citations, never tool output, and each opening is audited.

### 4.3 What answers in Chat

**Who it is for** (Phase 8.5): the live version's audience (everyone,
groups or named people), frozen with the version and reviewed with it.
Anyone outside it gets "not found" from every Chat route of that
assistant; someone dropped keeps their history and can't post. It
decides who chats, not what each person may read: that is knowledge
groups (9.4).

An assistant answers in Chat only from a version an admin approved (plan
D4, 7b.1). A builder submits; the submitter and the builder can't approve
it while another reviewer exists (7b.3). Approval freezes a manifest: the
documents, the connections and their permissions, the MCP servers and the
fingerprint of each tool. A turn on that version uses only those, leaves
out anything changed since, and approval refuses while anything has
drifted. Each conversation stays on the version it started on; pausing
and rollback are admins'. The status can't be edited, and a version whose
HTTP tool has no allowed sites can't be approved. Builders test drafts in
previews only they and admins see.

### 4.4 People, sessions and getting back in

- **Removing someone** (7b.4) ends their access on their next request,
  revokes every refresh token, closes their pending approvals, withdraws
  their invites and hands their assistants to someone who stays. The
  organisation always keeps an owner who can sign in.
- **An invite** is one per address, works only while its sender could
  still send it, and can be withdrawn. Signing up with one joins that
  organisation only.
- **Sessions:** a refresh token is used once (a lost reply gets 30
  seconds' grace); a switched-off account renews nothing; a password reset
  or change revokes every refresh token and refuses older access tokens.
- **Google sign-in** (8.3): the account is found by Google's id, never by
  address; an address that has an account is refused until its owner
  connects Google from Your account, signed in; new accounts only through
  an invite to the address or the owner's auto-join (Employees), with no
  password. State, nonce and PKCE bind the flow to the browser; Google's
  token is checked in full, the domain by its `hd` claim; the session
  tokens reach the page through a one-minute code after `#`.
- **Two-step sign-in** (8.4): an authenticator code (sealed secret, each
  code once) or a recovery code (hashed, each once) after the password;
  with `REQUIRE_ADMIN_2FA`, an owner or admin acts as an admin only in a
  session that passed it, and as a builder otherwise. Sign-in limits are
  per person.
- **A code from another device** (8.6): made only while signed in, eight
  characters from a 31-letter alphabet (about 40 bits), stored hashed,
  good once for five minutes, a newer one voiding the older, redeemed
  under the per-address sign-in limit, and both steps audited. It isn't a
  second factor: an account with an authenticator still finishes with its
  code. Its risk is someone talked into reading it out; the page says
  never to give it to anyone, IT included.
- **Recovery** (7b.6): one-time links, stored hashed, short-lived, a newer
  one voiding the older, with the token after `#` so no server log or
  Referer holds it; the reset form answers the same whether or not an
  account exists; opening a link changes nothing until the person acts.

### 4.5 Between organisations

With one organisation this wall matters less, and it stays: an archived
organisation, a personal one left from before 7b.8, or a server run with
`ORG_MODE=multi`.

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

### 4.6 Between the model and a database

The model writes SQL against the company's databases. Before anything runs (`datasources/sql_guard.py`):

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
5. a write the model writes goes to an approver (never the asker; Phase
   9.1), who sees the exact statement; what runs is the sealed copy they
   saw, checked against its hash, run once by the executor after every
   guard is applied again.

**Declared actions** (Phase 9.1) are statements the builder writes and an
admin approves with the version; the model only fills typed values, and
the asker's identity comes from the session. Values become literal nodes
in the parsed statement and are read back before anything runs. A
statement the asker may confirm alone must be provably about their own
rows (an asker slot in each inserted row, or `column = :asker_…` AND-ed
into the WHERE clause).

**Scoped to the asker** (Phase 9.3, Postgres): each statement runs in a
transaction that sets `app.asker_email` (a bound parameter, local to it)
for the database's row-level security policies. The model can't change
it: the guard refuses `SET`, `set_config` and `current_setting`. With
nobody signed in, the statement is refused rather than run unscoped. The
connection test fails for a role that row-level security doesn't bind
(superuser, `BYPASSRLS`, owner without `FORCE`), and names tables with no
policy, which are not filtered. The policies are the company's: a table
without one shows everyone's rows to everyone who asks.

The credential should still be a least-privilege role
(`docs/DATABASE_ACCESS.md`): the guard is the second wall, the database's
own permissions are the first.

### 4.7 Between the server and the network

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
- one message for "no such name" and "an internal name";
- **internal destinations** (Phase 9.3): a host the owner listed may
  resolve into its entry's range, on its ports, over http only if the
  entry says so. Never into this machine, link-local (metadata),
  unspecified or multicast addresses, the stack's own networks
  (`PLATFORM_NETWORKS`, fixed subnets in production), or what
  `APP_BASE_URL` resolves to. A suffix entry (`.corp.example.com`)
  covers names under it, not look-alikes.

**HTTP connections and declared calls** (Phase 9.3):

- The builder writes the method and path, and an admin approves them
  with the version. The model fills typed values only: each path value
  is URL-quoted into one segment (`.` and `..` refused), and the rest go
  in the query or a JSON body.
- The connection's headers are sealed under `APP_KEK` and sent only to
  its host (`allowed_domains=[host]`), with redirects never followed. The
  model never sees or sets them. `X-Asker-*` comes from the session, only
  when the connection says so; headers of those names can't be stored.
- A call the asker confirms needs a connection that tells the system who
  is asking. A stored request seals the call with the base URL it was
  asked for, and the executor refuses it if the connection moved.
- The manifest fingerprints each called connection, so a change after
  approval drops its calls until the next review.
- What the system does with the asker's identity is the system's: it
  should check its own permissions for that person, as with any client.

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
and port and the server connects (internal destinations don't apply to
them). The company's databases are on its
private network, so this cannot be limited to public addresses; it means
a builder can probe and connect to hosts the server can reach. Only
invited people build today, and the Employee role (8.1) will build
nothing; keep the server's own network reach as narrow as its job.

### 4.8 Around MCP servers

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

### 4.9 What reaches the browser

- **Uploaded files can't run as the app** (7a.1): the server decides the
  type from the bytes, signs the type and disposition into each link, and
  the proxy adds `nosniff` and a no-scripts policy on the files path.
- **Model output can't send data out** (7a.2): images in answers are never
  loaded, the app allows images only from itself, and links to other
  sites show their host. Markdown is rendered without raw HTML, and links
  can't be `javascript:`.
- **Security headers** on the API and the app: no framing, no referrer,
  `nosniff`.
- **The installable app's service worker** (8.6) answers only page loads
  that fail, with a static offline page; it caches no API response and no
  conversation. The offline page shows the organisation's steps as text,
  never markup.
- Still open: sign-in tokens in local storage (section 6).

### 4.10 Email

Messages only notify and link to a page where the thing is decided while
signed in, so a mail scanner opening a link changes nothing (7b.5). They
carry no conversation content and no free text; every name in them is
one line with links removed; plain text. SMTP settings are environment
only; the admin's test goes only to them. The record keeps no bodies, so
no invite link sits in the database. The user guide tells people that
Assistant Studio never asks them to approve anything or type a password
from an email.

### 4.11 Secrets

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
- Production (`APP_ENV=production`) refuses to start with a placeholder or short JWT secret, no
  KEK, no runner token, the default object-store password, or the
  local-only MCP flag.

### 4.12 Evidence

- **The audit log** records every change, every sign-in and failed
  sign-in, and every admin read of a conversation; a test fails when a
  route that changes something records nothing (7b.7). Entries keep the
  actor's email, so they still name a person whose account is gone.
- **It can't be quietly edited.** A trigger refuses changes and deletes
  from the app; entries form a SHA-256 hash chain, sealed each minute;
  the owner's integrity check finds a changed, removed or inserted entry,
  and shows the newest hash to keep off the server.
- **The proxy's access log** (183 days) and the API's access lines name
  who connected, from where, to what (7a.8).

### 4.13 Spend and abuse

**Per person** (Phase 8.9): each person's spend is recorded, and an
admin's per-person limit stops one person without stopping anyone else.
Employees have a smaller message rate. Only admins see one person's spend.
When something stops Chat for everyone (the provider's own limit,
credentials, the organisation's budget, capacity), the admins are emailed
and shown a banner.

Budgets per org and assistant, a cap per conversation, rate limits per
address, per user and per org (tighter on sign-in, chat, the AI helpers
and anything that starts a job), bounded turn concurrency, and size caps
on suites, uploads and tool output.

## 5. What the passes found and fixed

### 5.1 The Phase 6 pass

Five reviewers, in task 6.3, each took one surface and tried to break it; everything
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

### 5.2 Phase 7: the gap review's findings

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
Fixed by the Studio floor described in section 4.1, applied in the shared
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
warning. Since 7b.1 a version with this tool and no list can't be
approved.

**Phase 7b.1: no approval, and nothing an approval could hold (found by
the gap review).** Publishing made a builder's draft what everyone got, in
every open conversation, at once; a builder could also mark an assistant
published, or reopen one an admin had archived, with a plain edit; with
nothing published, Chat ran the draft. And a version kept only its config,
so after any approval a builder could still change live behaviour through
a reindexed document, a connection pointed elsewhere or given writes, or
an MCP tool whose description changed. Now a version is submitted and an
admin approves it; only approval and an admin's rollback move what Chat
answers from; the status is not editable; Chat refuses with no approved
version or while paused; each conversation stays on its version; and each
version records a manifest of what it uses when it is submitted. Turns on
a version search only its approved documents (a reindex keeps the old
copy while a version needs it) and leave out any connection, server or
tool that has changed since; approving refuses while anything has
drifted. Builders test drafts in previews only they and admins see.
Still open: an admin can approve their own submission (7b.3), and
catalog text is edited outside versions (Phase 8).

**Phase 7b.2: every member could read every conversation (found by the
gap review).** Lists, messages, runs and approvals were open to any
member, Usage named every conversation by its title (the start of its
first message), and a rename put old and new titles in the audit log.
Now each person lists and reads only their own. An admin or the owner may
read any by giving a reason; one check on every conversation read records
it and commits the record before the content is returned (one row per
admin, conversation and reason in 30 minutes, holding the reason and the
reader, never the content). A test fails on any conversation route that
skips the check. Other people's titles are left out of lists and Usage,
and the audit log stores no titles.

**Phase 7b.3: approvals that could be one person's (found by the gap
review).** An admin could approve the version they submitted, of the
assistant they built, and a tool-call decision didn't say whether the
asker decided it. Now the submitter and the builder can't approve while
another active admin or the owner could; the sole owner may, with a note,
recorded as self-approved. Tool-call approvals stay with the asker until
Phase 9 (D17), and each decision records who asked, who decided, their
role and whether they were the same person. Still open: a write can be
approved by the person who asked for it, until Phase 9.

**Phase 7b.4: leavers kept everything (found by the gap review).** Nobody
could be removed, so a leaver kept reading the org, kept renewing their
session, and could have a pending write approved after leaving; an
invite couldn't be withdrawn and stayed valid after its sender was
demoted. Now deactivating someone ends their access and revokes every
refresh token in one transaction, closes their pending approvals,
revokes their unused invites and hands their assistants to someone who
stays; a switched-off account renews nothing; an invite works only while
its sender could still send it, and can be withdrawn; an org always keeps
an owner who can sign in.

**Phase 7b.5: email, designed not to be a way in.** Messages only notify
and link to a page where the thing is decided while signed in, so a mail
scanner opening a link changes nothing; they carry no conversation
content and no free text, and every name in them is one line with links
removed, so it can't forge a header or plant a link; plain text. SMTP
settings are environment only, so nobody can point the server at their
own mail server; the admin test goes only to the admin asking. The record
keeps no bodies, so no invite link sits in the database. The preflight
refuses logging mail in production and warns about a personal Gmail
account. Residual: the mailbox's provider sees each message (recipient,
subject, the link); use a company mailbox.

**Phase 7b.6: no way back in, and a changed password left sessions
running.** Password reset, change and address checks now use one-time
links stored hashed, used once in a conditional update, short-lived, a
newer one voiding the older, with the token after `#` so it reaches no
server log or Referer. The reset form answers the same whether or not an
account exists, and is rate-limited per address and IP. A reset or change
revokes every refresh token and makes older access tokens fail, and
emails a notice. Opening a link changes nothing until the person acts.

**Phase 7b.7: an audit log that could be quietly edited, and had gaps.**
The append-only trigger stopped the app, not someone with the database's
owner role, who could drop it and change or remove rows unseen. Entries
now form a SHA-256 hash chain, sealed by the worker; the trigger allows
only that one-time seal. The owner's integrity check finds a changed,
removed or inserted entry. Rewriting the whole chain after an edit is
caught only against a hash kept elsewhere, which the check shows to
copy. Sign-ins, failed sign-ins, sign-outs and the remaining unrecorded
changes are recorded, and a test fails when a new route that changes
something records nothing. Entries keep the actor's email, so they name
the person after the account is gone. Export and the check are the
owner's: admins are among those the log watches. Residual: until the
newest hash is copied out of the server, a database owner can rewrite the
chain undetected.

**Phase 7b.8: anyone could create an organisation of their own.** Any
signed-in person could make an org, and every uninvited sign-up got one:
a place outside the company's admins, budgets and audit log, on the
company's model key. Production now runs one organisation
(`ORG_MODE=single`): nobody creates another, and sign-up is by invite
after the first account. The upgrade archives the empty personal orgs
left from before (nothing deleted, nobody left without an org), and the
operator's report lists the others with their spend. An archived org is
refused by the one membership check every org route uses. Residual: a
server set to `ORG_MODE=multi` keeps the old behaviour; the preflight
warns about it in production.

**Phase 7b.9: the defences that live in Postgres were tested only by
hand.** The append-only trigger, pgvector and full-text retrieval, and
the Redis approval paths ran in a script at the end of each phase, and a
missing database read as a pass. CI now runs that tier on every pull
request against the same datastores as development, fails when a
required service is missing, checks the models against the Postgres
schema, and takes Phase 7's migrations down and back up with data.

### 5.3 Phase 9.9: the second pass, before the full pilot

One reviewer per new wall (the Employee role and audiences, knowledge
groups in retrieval and citations, identity given to tools, the approval
queue, Google sign-in, shared links), plus everything Phase 9 and 10
added. It was done by reading code. Every finding is fixed, tested in
`tests/test_security_pass_v2.py` and `test_google_signin.py`.

| # | Finding | Severity | Status |
| - | ------- | -------- | ------ |
| 1 | A stolen session could attach the attacker's Google account to the victim's, a lasting way in that skipped the authenticator | high | **Fixed:** the Google email must equal the account's; someone with an authenticator confirms with it before a link ticket |
| 2 | `content-url` handed out restricted or held knowledge files to anyone who could use the assistant | high | **Fixed:** only readers of that source; a held source only to editors |
| 3 | Eval suites and runs (answers from restricted knowledge, questions from the review queue) were readable by every member | medium-high | **Fixed:** suites, their runs and run details are editor-only |
| 4 | The free-form HTTP tool let the model send `X-Asker-*` headers to an internal system | medium-high | **Fixed:** model-supplied `X-Asker-*` headers are dropped; only connections send them, from the session |
| 5 | A co-editor could point an HTTP connection at their own server and receive its sealed headers | medium | **Fixed:** a new address (HTTP) or a new host, port, database or user (databases) needs the credential entered again (`headers_required`, `password_required`) |
| 6 | `:asker_name` counted as pinning a confirm-tier write, but a name is chosen by the person | medium | **Fixed:** only `:asker_email` and `:asker_id` pin |
| 7 | In a preview every request goes to the asker (the builder), DDL included | low | **Fixed:** in a preview, a schema change the model wrote goes to an approver; statements that switch row-level security off never run (the guard refuses them); the builder still confirms their own test writes (the 9.1 choice) |
| 8 | An eval conversation can still be posted into, outside the audience and personal cap | low | **Fixed:** an eval run's conversation can't be replied in (`eval_conversation`) |
| 9 | Typed passwords were masked only in chat messages | low | **Fixed:** typed passwords are also masked in feedback comments, hand-off notes and conversation titles |
| 10 | After access is lost, `kb_search` tool outputs in old messages still show their text to builders and admins | low | **Fixed:** a reader who can't read every source of the assistant gets knowledge search results hidden too, in the conversation and its export |
| 11 | A shared conversation on an older version, answered without citations, can carry restricted text | low | **Fixed:** a share also needs the reader to read every restricted source the conversation's version answered from, cited or not |
| 12 | An approval's result (another system's body) reaches the next prompt framed as a platform note | low | **Fixed:** results and approvers' notes reach the model inside `<untrusted_output>`, marked as data to report, not instructions |

Walls tried and found holding:

- employees kept out of the Studio;
- other people's conversations need the starter, or an admin's recorded
  read;
- retrieval, subagents and the source list narrowed per asker, with the
  access epoch;
- `app.asker_email` bound and transaction-local, with the guard refusing
  `SET` and `set_config`;
- SSRF pinning and the platform's own networks;
- the approval hash, run-once, and re-checks;
- Google's state, nonce, PKCE and `hd`;
- share-link tokens, revocation and leavers;
- attachment types decided by bytes;
- analytics and traffic counts only;
- names kept out of the review queue;
- admin-only approval evidence;
- export following the reader rule;
- hand-off emails without content.

## 6. What is still open

Known, and either accepted for one company on its own network or
waiting for a phase. They are the honest edge of what the platform
defends.

**People and access**

1. **Admins can read every conversation** (plan D5, by design). Each read
   is recorded with its reason before anything is shown, but the reason
   is free text: keep admins few, and look at **Admins opening
   conversations** on the Audit log page now and then.
2. **An approver decides what the assistant names them for** (Phase
   9.1): a group or people an admin chose, and whoever they hand over to
   while away (at most 90 days). They see each request's exact input, not
   the conversation. Keep the lists short; stand-ins are in the audit log.
   The only owner may decide their own request when nobody else can, with
   a recorded reason.
3. **A stolen session token is enough** for an admin's actions within its
   life (Phase 8.4 put a second factor on signing in, not yet on each
   risky action: step-up is planned). Without `REQUIRE_ADMIN_2FA`, a
   phished admin password alone is.
4. **A builder is still trusted with a lot.** Invites make employees
   now (8.1), who build nothing; a `member` can create assistants, add
   database connections to any host the server reaches, URL sources and
   remote MCP servers, and spend. Make builders only of people who need
   it.
5. **Sign-in tokens live in the browser's local storage.** There is no
   script-source policy on the app (Next would need per-request nonces),
   so an XSS bug could read them. Model output and uploaded files, the
   two surfaces found, are handled (4.9).
6. **Logging out does not end the 15-minute access token**, only the
   refresh token. Removing someone, switching off their account and
   changing a password take effect at once.
7. **Email verification is recorded, not required.** Identity given to
   tools must refuse an unverified account (Phase 9.3).

**Where conversations also live**

8. **Traces.** With `LANGFUSE_*` or `OTEL_EXPORTER_OTLP_ENDPOINT` set,
   full prompts and answers go to that service, outside the audited read
   path. Leave them unset, or give only the owner access.
9. **Backups and the agent-state volume** hold every conversation in
   full; the CLI's transcripts are unredacted while their conversation
   lives (7a.5). Whoever holds the backups reads everything.
10. **What leaves the building** (plan D18): every turn goes to
    Anthropic; documents go to Voyage unless `RAG_OFFLINE=1`; web-search
    queries go to the search provider, and **a steered model can put data
    into a search query** (a per-assistant policy is Phase 9.4); the
    mailbox provider sees each email's recipient, subject and link.
    Personal-data redaction covers search queries and trace spans, in a
    limited set of formats.

**The platform**

11. **Whether the server faces the internet is the operator's setup**
    (8.2): `TLS_MODE`, `BIND_IP`, the DNS and the firewall. The preflight
    reviews what it can see from the server; that 80 and 443 are closed
    from outside is checked by hand (OPERATIONS §2). A DNS credential with
    more than one record's reach lets whoever holds it issue a
    certificate for the name: prefer acme-dns.
12. **The MCP runner is one trust zone.** Every local-command server runs
    as the same user in the same container, sharing `/tmp` and `/proc`. A
    malicious server can read another server's environment (its decrypted
    secrets) and the runner's token. Only admins add commands; add only
    servers you trust, and keep the runner without egress
    (`docker-compose.mcp-egress.yml`) unless you need it.
13. **A server's own "read-only" hint is trusted** when a builder sets an
    MCP server's approval to automatic. The default is to ask; keep it for
    servers you don't control.
14. **The audit chain is checked only against itself** unless its newest
    hash is kept off the server (7b.7). Someone who owns the database can
    rewrite it end to end. Copy the hash the owner's integrity check
    shows, say monthly; sending it out automatically is planned.
15. **A sealed secret is bound to its kind, not its owner.** Someone who
    can write to the database (but has no KEK) could attach another
    connection's sealed password to a connection they control.
16. **No size cap on what a remote MCP server sends** before it is parsed.
17. **MySQL optimizer hints** in a read are passed through (bounded by the
    adapter's own timeout), and a `CREATE FUNCTION` body is not checked
    against the table lists (it needs schema-change permission and a
    person's approval).
18. **`vitest` has two moderate advisories** (dev tooling only; the fix is
    a breaking major upgrade).
19. **`ORG_MODE=multi`** brings back what 7b.8 closed: anyone signed in
    can create an organisation, and an uninvited sign-up gets one. The
    preflight warns about it in production.

## 7. Before Chat opens to employees

The security baseline (plan D19) and the Phase 8 items it depends on. Chat
opens to employees only when every line is done.

Done in Phase 7:

- [x] Authorisation denies by default, with every role tested against
      every route (7a.3).
- [x] Uploaded files can't run as the app; model output can't send data
      out through images or links (7a.1, 7a.2).
- [x] Admins read conversations but can't act in them (7a.4); each
      person's conversations are private, and every admin read is
      recorded (7b.2).
- [x] Only approved, frozen versions answer, and nobody approves their own
      (7b.1, 7b.3).
- [x] Every session can be ended: removal, a switched-off account, a
      password change (7b.4, 7b.6).
- [x] A complete, tamper-evident audit log (7b.7); one organisation
      (7b.8); the Postgres and Redis defences tested on every change
      (7b.9).

Still to do:

- [x] The Employee role, and the server deciding what an employee
      receives: no SQL, rows or costs in their responses (8.1).
- [x] The internal hosting: office network and VPN only, DNS-challenge
      certificates (8.2). The operator sets it up and checks it from each
      place (OPERATIONS §2).
- [x] Google sign-in keyed on Google's id, never linked automatically to
      a password account, first sign-in by invite or opt-in auto-join
      (8.3).
- [x] Two-factor sign-in for the owner and admins (8.4).
- [x] Audiences: employees see only the assistants published to them
      (8.5).
- [x] The Chat app: answers survive a dropped connection, nothing typed
      is lost, errors in an employee's words, links survive sign-in, sign-in
      on phones and an installable app (8.6).
- [x] A privacy notice and acceptable-use acknowledgement before the
      first chat, saying what leaves the building (8.7, plan D18;
      docs/DATA_FLOWS.md).
- [x] One purge path and the retention rules (8.10): nightly retention,
      erasing one person, legal holds, erasures re-applied after a restore,
      traces metadata-only in production.
- [ ] Per-person spending caps (8.9).
- [x] Requests that wait safely: nothing decided by its asker except a
      declared action on their own rows, the exact input sealed, run once
      after every guard is checked again (9.1).
- [x] Browser tests of the employee flows (8.11): `npm run e2e -w web`, in
      CI and the phase-end check.

Before the full pilot (end of Phase 9): a second security pass like task
6.3 (plan 9.9). Each reviewer tries one new wall: the Employee role and
audiences, knowledge groups in retrieval and citations, identity given
to tools, the action-approval queue, Google sign-in, shared links. Their
findings go in section 5.

## 8. For operators

- Set `APP_ENV=production`; it turns on the start-up checks in 4.11.
- Keep `ORG_MODE=single` and `REGISTRATION=invite` (the production
  compose defaults).
- Set it up as an internal host (OPERATIONS §2): `TLS_MODE=acmedns`, a
  `BIND_IP`, the name answered privately; rebuild the proxy image monthly.
- Give every database connection a least-privilege role
  (`docs/DATABASE_ACCESS.md`).
- Leave `DB_SQLITE_DIR` empty unless you need SQLite connections; if you
  do, point it at a folder that holds only those files.
- Keep Postgres, Redis and MinIO off public interfaces. Redis carries
  approval decisions and the job queue. The production compose file
  publishes none of them and gives Redis a password; the development one
  publishes all three with default credentials.
- Keep `MCP_ALLOW_INSECURE_URLS` off (production refuses it).
- With Google sign-in, set `GOOGLE_ALLOWED_DOMAIN`, and keep the consent
  screen Internal (OPERATIONS §3).
- Keep `REQUIRE_ADMIN_2FA=true`, and keep `scripts.reset_two_factor` to the
  operator, after checking who is asking.
- Leave `LANGFUSE_*` and `OTEL_EXPORTER_OTLP_ENDPOINT` unset, or limit
  them to the owner (section 6, item 8).
- Send email from a company mailbox, not a personal one (the preflight
  warns).
- Copy the audit log's newest hash off the server monthly (OPERATIONS §8).
- Keep the backups and the `APP_KEK` apart, and both off the server.
- Run `pip-audit` and `npm audit` on upgrades; CI fails on either.

## Appendix: who may call each route

Written from `ROLE_MATRIX` in `tests/test_role_matrix.py`, which walks
each route as each role; a test fails when this list and the matrix
differ.

<!-- role-matrix:start (written by `python -m tests.role_table`) -->

### Anyone, signed in or not

- `POST /api/v1/auth/device-code/redeem`
- `GET /api/v1/auth/google/callback`
- `POST /api/v1/auth/google/exchange`
- `GET /api/v1/auth/google/start`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/login/two-factor`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/password-reset`
- `POST /api/v1/auth/password-reset/confirm`
- `GET /api/v1/auth/providers`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/verify-email`
- `GET /api/v1/invites/{token}`
- `GET /api/v1/meta/config-schema`
- `GET /api/v1/meta/graph-schema`
- `GET /api/v1/meta/samples`
- `GET /healthz`
- `GET /readyz`

### Anyone signed in, about themselves

- `DELETE /api/v1/approvals/delegations/{delegation_id}`
- `POST /api/v1/auth/device-code`
- `DELETE /api/v1/auth/google`
- `POST /api/v1/auth/google/link-ticket`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/password`
- `GET /api/v1/auth/two-factor`
- `POST /api/v1/auth/two-factor/confirm`
- `POST /api/v1/auth/two-factor/disable`
- `POST /api/v1/auth/two-factor/recovery-codes`
- `POST /api/v1/auth/two-factor/setup`
- `POST /api/v1/auth/two-factor/verify`
- `POST /api/v1/auth/verify-email/send`
- `POST /api/v1/invites/{token}/accept`
- `GET /api/v1/orgs`
- `POST /api/v1/orgs`
- `GET /api/v1/shared/{token}`

### Any member of the organisation

- `GET /api/v1/assistants`
- `POST /api/v1/assistants`
- `GET /api/v1/assistants/{assistant_id}`
- `GET /api/v1/assistants/{assistant_id}/budget`
- `GET /api/v1/assistants/{assistant_id}/data-sources`
- `GET /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}`
- `GET /api/v1/assistants/{assistant_id}/db-connections`
- `GET /api/v1/assistants/{assistant_id}/db-connections/{connection_id}`
- `GET /api/v1/assistants/{assistant_id}/db-connections/{connection_id}/schema`
- `GET /api/v1/assistants/{assistant_id}/draft-graph`
- `GET /api/v1/assistants/{assistant_id}/editors`
- `GET /api/v1/assistants/{assistant_id}/eval-suites`
- `POST /api/v1/assistants/{assistant_id}/graph:compile`
- `POST /api/v1/assistants/{assistant_id}/graph:validate`
- `GET /api/v1/assistants/{assistant_id}/listing`
- `GET /api/v1/assistants/{assistant_id}/mcp-servers`
- `GET /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}`
- `GET /api/v1/assistants/{assistant_id}/traffic`
- `GET /api/v1/assistants/{assistant_id}/versions`
- `GET /api/v1/assistants/{assistant_id}/versions/diff`
- `GET /api/v1/assistants/{assistant_id}/versions/{number}`
- `GET /api/v1/assistants/{assistant_id}/versions/{number}/drift`
- `POST /api/v1/assistants:from-sample`
- `GET /api/v1/mcp-presets`
- `GET /api/v1/mcp-runner`
- `GET /api/v1/orgs/{org_id}/budgets`
- `GET /api/v1/orgs/{org_id}/groups`
- `GET /api/v1/orgs/{org_id}/members`
- `GET /api/v1/orgs/{org_id}/usage`
- `POST /api/v1/pipeline:recommend`

### Whoever may edit the assistant: its creator, its co-editors, admins and the owner

- `DELETE /api/v1/assistants/{assistant_id}`
- `PATCH /api/v1/assistants/{assistant_id}`
- `POST /api/v1/assistants/{assistant_id}/actions:check`
- `GET /api/v1/assistants/{assistant_id}/analytics`
- `GET /api/v1/assistants/{assistant_id}/approvers`
- `POST /api/v1/assistants/{assistant_id}/data-sources`
- `POST /api/v1/assistants/{assistant_id}/data-sources/upload`
- `DELETE /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}`
- `PUT /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}/access`
- `POST /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}:confirm-everyone`
- `POST /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}:reindex`
- `POST /api/v1/assistants/{assistant_id}/db-connections`
- `DELETE /api/v1/assistants/{assistant_id}/db-connections/{connection_id}`
- `PATCH /api/v1/assistants/{assistant_id}/db-connections/{connection_id}`
- `POST /api/v1/assistants/{assistant_id}/db-connections/{connection_id}:refresh-schema`
- `POST /api/v1/assistants/{assistant_id}/db-connections/{connection_id}:test`
- `PUT /api/v1/assistants/{assistant_id}/draft-config`
- `PUT /api/v1/assistants/{assistant_id}/draft-graph`
- `POST /api/v1/assistants/{assistant_id}/editors`
- `DELETE /api/v1/assistants/{assistant_id}/editors/{user_id}`
- `POST /api/v1/assistants/{assistant_id}/eval-suites`
- `GET /api/v1/assistants/{assistant_id}/feedback`
- `GET /api/v1/assistants/{assistant_id}/http-connections`
- `POST /api/v1/assistants/{assistant_id}/http-connections`
- `DELETE /api/v1/assistants/{assistant_id}/http-connections/{http_connection_id}`
- `PATCH /api/v1/assistants/{assistant_id}/http-connections/{http_connection_id}`
- `PUT /api/v1/assistants/{assistant_id}/listing`
- `POST /api/v1/assistants/{assistant_id}/mcp-servers`
- `DELETE /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}`
- `PATCH /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}`
- `POST /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}:discover-tools`
- `POST /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}:health`
- `POST /api/v1/assistants/{assistant_id}/pipeline:recommend`
- `POST /api/v1/assistants/{assistant_id}/prompt:generate`
- `GET /api/v1/assistants/{assistant_id}/review-queue`
- `POST /api/v1/assistants/{assistant_id}/review-queue/{item_id}:dismiss`
- `POST /api/v1/assistants/{assistant_id}/review-queue/{item_id}:to-case`
- `POST /api/v1/assistants/{assistant_id}/versions`
- `POST /api/v1/assistants/{assistant_id}/versions/{number}/withdraw`
- `GET /api/v1/eval-runs/{run_id}`
- `POST /api/v1/eval-runs/{run_id}:cancel`
- `DELETE /api/v1/eval-suites/{suite_id}`
- `GET /api/v1/eval-suites/{suite_id}`
- `PATCH /api/v1/eval-suites/{suite_id}`
- `DELETE /api/v1/eval-suites/{suite_id}/cases/{case_id}`
- `PUT /api/v1/eval-suites/{suite_id}/cases/{case_id}`
- `POST /api/v1/eval-suites/{suite_id}/cases:bulk`
- `GET /api/v1/eval-suites/{suite_id}/runs`
- `POST /api/v1/eval-suites/{suite_id}/runs`

### The person who started the conversation; an admin or the owner, giving a reason that is recorded first

- `GET /api/v1/conversations/{conversation_id}/runs`
- `GET /api/v1/conversations/{conversation_id}/runs/{run_id}`

### Chat: every member, employees included (an assistant that answers in Chat)

- `GET /api/v1/approvals/decidable`
- `GET /api/v1/approvals/decided`
- `GET /api/v1/approvals/delegations`
- `POST /api/v1/approvals/delegations`
- `GET /api/v1/approvals/mine`
- `GET /api/v1/approvals/summary`
- `GET /api/v1/assistants/{assistant_id}/conversations`
- `POST /api/v1/assistants/{assistant_id}/conversations`
- `GET /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}/content-url`
- `DELETE /api/v1/assistants/{assistant_id}/memories`
- `GET /api/v1/assistants/{assistant_id}/memories`
- `GET /api/v1/chat/access`
- `GET /api/v1/chat/assistants`
- `GET /api/v1/chat/assistants/{assistant_id}`
- `GET /api/v1/chat/assistants/{assistant_id}/about`
- `GET /api/v1/chat/conversations`
- `GET /api/v1/chat/help`
- `GET /api/v1/chat/notice`
- `POST /api/v1/chat/notice:accept`

### Chat, reading a conversation: the person who started it, employees included; an admin or the owner, giving a reason that is recorded first

- `GET /api/v1/conversations/{conversation_id}`
- `GET /api/v1/conversations/{conversation_id}/approvals`
- `GET /api/v1/conversations/{conversation_id}/attachments/{attachment_id}/content`
- `GET /api/v1/conversations/{conversation_id}/export`
- `GET /api/v1/conversations/{conversation_id}/handoffs`
- `GET /api/v1/conversations/{conversation_id}/messages`
- `GET /api/v1/conversations/{conversation_id}/shares`
- `GET /api/v1/conversations/{conversation_id}/turn`
- `GET /api/v1/conversations/{conversation_id}/turns/{turn_id}/events`

### Chat, acting in a conversation: the person who started it, employees included

- `POST /api/v1/approvals/{approval_id}:withdraw`
- `DELETE /api/v1/conversations/{conversation_id}`
- `PATCH /api/v1/conversations/{conversation_id}`
- `POST /api/v1/conversations/{conversation_id}/attachments`
- `POST /api/v1/conversations/{conversation_id}/handoff`
- `POST /api/v1/conversations/{conversation_id}/messages`
- `PUT /api/v1/conversations/{conversation_id}/messages/{message_id}/feedback`
- `PUT /api/v1/conversations/{conversation_id}/outcome`
- `POST /api/v1/conversations/{conversation_id}/shares`
- `DELETE /api/v1/conversations/{conversation_id}/shares/{share_id}`
- `POST /api/v1/conversations/{conversation_id}:interrupt`
- `POST /api/v1/conversations/{conversation_id}:restore`

### One of the assistant's approvers (admins and the owner while it names nobody), never the person who asked

- `POST /api/v1/approvals/{approval_id}:resolve`

### One request: the person who asked, whoever may decide it or decided it, admins and the owner

- `GET /api/v1/approvals/{approval_id}`

### Admins and the owner

- `PUT /api/v1/assistants/{assistant_id}/approvers`
- `POST /api/v1/assistants/{assistant_id}/archive`
- `PUT /api/v1/assistants/{assistant_id}/budget`
- `POST /api/v1/assistants/{assistant_id}/unarchive`
- `POST /api/v1/assistants/{assistant_id}/versions/{number}/approve`
- `GET /api/v1/assistants/{assistant_id}/versions/{number}/evidence`
- `POST /api/v1/assistants/{assistant_id}/versions/{number}/make-live`
- `POST /api/v1/assistants/{assistant_id}/versions/{number}/reject`
- `POST /api/v1/assistants/{assistant_id}:official`
- `GET /api/v1/orgs/{org_id}/alerts`
- `POST /api/v1/orgs/{org_id}/alerts:resolve`
- `GET /api/v1/orgs/{org_id}/analytics`
- `GET /api/v1/orgs/{org_id}/analytics.csv`
- `GET /api/v1/orgs/{org_id}/approvals`
- `GET /api/v1/orgs/{org_id}/audit-log`
- `PUT /api/v1/orgs/{org_id}/budgets`
- `GET /api/v1/orgs/{org_id}/budgets/people`
- `PUT /api/v1/orgs/{org_id}/budgets/people`
- `GET /api/v1/orgs/{org_id}/erasures`
- `POST /api/v1/orgs/{org_id}/groups`
- `DELETE /api/v1/orgs/{org_id}/groups/{group_id}`
- `PATCH /api/v1/orgs/{org_id}/groups/{group_id}`
- `GET /api/v1/orgs/{org_id}/groups/{group_id}/members`
- `POST /api/v1/orgs/{org_id}/groups/{group_id}/members`
- `DELETE /api/v1/orgs/{org_id}/groups/{group_id}/members/{user_id}`
- `GET /api/v1/orgs/{org_id}/holds`
- `GET /api/v1/orgs/{org_id}/invites`
- `POST /api/v1/orgs/{org_id}/invites`
- `DELETE /api/v1/orgs/{org_id}/invites/{invite_id}`
- `GET /api/v1/orgs/{org_id}/mail`
- `POST /api/v1/orgs/{org_id}/mail:test`
- `PATCH /api/v1/orgs/{org_id}/members/{user_id}`
- `PUT /api/v1/orgs/{org_id}/members/{user_id}/budget`
- `GET /api/v1/orgs/{org_id}/members/{user_id}/leaving`
- `POST /api/v1/orgs/{org_id}/members/{user_id}:deactivate`
- `POST /api/v1/orgs/{org_id}/members/{user_id}:reactivate`
- `GET /api/v1/orgs/{org_id}/notice`
- `GET /api/v1/orgs/{org_id}/notice/revisions`
- `PUT /api/v1/orgs/{org_id}/retention`
- `GET /api/v1/orgs/{org_id}/settings`

### The owner alone

- `GET /api/v1/orgs/{org_id}/audit-log.csv`
- `GET /api/v1/orgs/{org_id}/audit-log/integrity`
- `POST /api/v1/orgs/{org_id}/holds`
- `POST /api/v1/orgs/{org_id}/holds:release`
- `POST /api/v1/orgs/{org_id}/members/{user_id}:erase`
- `PUT /api/v1/orgs/{org_id}/notice`
- `PATCH /api/v1/orgs/{org_id}/settings`

<!-- role-matrix:end -->
