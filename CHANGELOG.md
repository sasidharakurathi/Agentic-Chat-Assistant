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

### Action needed

- **Pointing a connection somewhere else needs its credential again**
  (the second security pass, EXPLAINER §19, 9.9). Changing a database
  connection's host, port, database or user, or an HTTP connection's
  address, now asks for the password or headers again
  (`password_required`, `headers_required`). Eval suites and runs are now
  only for the assistant's editors, eval conversations can't be replied
  in, and connecting Google needs an account with the same email.
- **Builder testing no longer spends the pilot's budget** (9.7). If you
  relied on the org budget to cap previews and eval runs, set a builder
  budget in Members › Organisation.
- **The production networks now have fixed address ranges** (EXPLAINER
  §19, 9.3): `172.31.240.0/24`, `.241.0/24` and `.242.0/24` for `edge`,
  `data` and `mcp`, which the API is told about (`PLATFORM_NETWORKS`) so
  it never reaches its own datastores. If they clash with your office or
  VPN ranges, set `EDGE_SUBNET`, `DATA_SUBNET` and `MCP_SUBNET` in
  `.env.production`. Docker keeps a network's range once it exists, so
  run `docker compose -f docker-compose.prod.yml down` (volumes are kept)
  before the next `up` (OPERATIONS, "Company systems").
- **Requests that need a person no longer wait inside the answer**
  (EXPLAINER §19, 9.1). `APPROVAL_TIMEOUT_S` is gone: an approver's
  request waits for the organisation's expiry (72 hours by default,
  Members › Organisation), with one reminder. In production the `mailer`
  service now runs approved requests too: keep it running. Requests
  waiting before the upgrade are closed as expired.
- **Rehearse the upgrade on a copy of your database first** (OPERATIONS
  §7). The 7b releases change who can chat with what, and migrations go
  one way. Then run `python -m scripts.upgrade_report` and read it before
  and after upgrading the real one.
- **Production now runs one organisation** (`ORG_MODE=single` in
  `docker-compose.prod.yml`; EXPLAINER §17, 7b.8): nobody can create a
  second one, and sign-up is by invite after the first account whatever
  `REGISTRATION` says. To keep several, set `ORG_MODE=multi`.
- **Personal orgs are sorted out by the upgrade:** one that others joined
  becomes the team's; an empty one whose people have another org is
  archived (nothing deleted). Personal orgs with something in them are
  listed by the report, to archive, freeze or keep.

### Changed

- **The rest of the second security pass, and what the first builds left**
  (EXPLAINER §19, 9.9):
  - **Knowledge:** answers written from knowledge someone lost are
    hidden, and so are their search results.
  - **Previews:** a schema change the model wrote waits for an
    approver.
  - **Hand-off:** an email contact must be an admin.
  - **Screenshots:** served through the API (`GET
/conversations/{id}/attachments/{attachment_id}/content`; the
    presigned-link route is gone), and unsent ones are deleted after a
    day.
  - **Evals:** cases can ask follow-ups (`follow_ups`), and results carry
    `turns`.
- **Insight (Phase 10, EXPLAINER §19, 10.1 to 10.5):**
  - **Analytics** per assistant (resolution, cost per resolved, compared
    with the previous period) and a per-assistant report for admins, with
    a CSV download.
  - **Traffic drawn on the canvas.**
  - **A needs-review queue** that turns complaints into test cases and
    emails the person once it is fixed.
  - **Approval evidence:** eval scorecard, "no unapproved write" and a
    cost forecast; eval cases can require an approval.
  - **Erasure counts,** and conversation export.
  - **For API clients:**
    - `GET /assistants/{id}/analytics`;
    - `GET /orgs/{org}/analytics` (and `.csv`);
    - `GET /assistants/{id}/traffic`;
    - `GET /assistants/{id}/review-queue`, with `POST .../{item_id}:to-case`
      and `:dismiss`;
    - `GET /assistants/{id}/versions/{n}/evidence`;
    - `GET /conversations/{id}/export`;
    - `details` on erasures.
  - Migration `ca03b4c5d6e7`.
- **Hand-off, screenshots and builder budgets (EXPLAINER §19, 9.5 to
  9.8):**
  - **Hand-off.** Hand a conversation to a person: an email that only
    links, or a ticket through a declared HTTP action.
  - **Screenshots.** Paste or attach screenshots in Chat.
  - **Typed secrets.** Passwords and keys typed into Chat are masked
    before anything stores or sends them.
  - **Builder budget.** Previews and eval runs spend from their own
    optional monthly builder budget, and the pilot's budgets count live
    spend only.
  - **The IT helpdesk sample** comes first, with `python -m
scripts.demo_helpdesk` for its demo database.
  - **For API clients:**
    - `POST /conversations/{id}/handoff` and `GET .../handoffs`;
    - `POST /conversations/{id}/attachments`, `GET
.../attachments/{attachment_id}`, and `attachment_ids` on messages;
    - `builder` on budgets;
    - `builder_monthly_usd` in settings;
    - `handoff` in the config.
  - Migrations `c9a5b6c7d8e9` and `c9a6b7c8d9e0`.
- **Knowledge groups, revocation, the ingest scan and share links**
  (EXPLAINER §19, 9.4):
  - **Who reads a source.** Each knowledge source is everyone's or
    readable by groups (deny by default). Answers and citations use only
    what the asker may read, and an assistant is offered only to people
    who can read all of its restricted sources.
  - **Losing access.** A conversation on restricted knowledge starts
    fresh after someone loses access, and earlier citations hide.
  - **The scan.** A source open to everyone that holds a credential, or
    several Aadhaar, PAN or card numbers, waits until its builder chooses
    groups or confirms (audited).
  - **Share links.** Conversations can be shared as read-only links
    inside the organisation, re-checked at each opening and audited.
  - **For API clients:**
    - `PUT /assistants/{id}/data-sources/{id}/access`;
    - `POST .../{id}:confirm-everyone`;
    - `everyone`, `group_ids` and the `held` status on sources;
    - `ingest_report.sensitive`;
    - `POST`/`GET /conversations/{id}/shares`;
    - `DELETE /conversations/{id}/shares/{share_id}`;
    - `GET /shared/{token}`.
  - Migration `c9a4b5c6d7e8`.
- **Data scoped to the asker, company systems over HTTP, and internal
  destinations** (EXPLAINER §19, 9.3):
  - **A Postgres connection can answer only for the person asking.**
    Each statement sets `app.asker_email` for the database's row-level
    security, and it refuses with nobody signed in. Testing it checks
    that row-level security binds the role.
  - **HTTP connections and declared calls.** A connection holds a company
    system's address and sealed headers, and can say who is asking. The
    HTTP request tool can carry declared calls on it, with the tiers of
    SQL actions.
  - **Internal destinations.** The owner lists the internal systems that
    assistants may reach (host, address range, ports, plain http). The
    server's own networks are always refused.
  - **For API clients:**
    - `GET`/`POST /assistants/{id}/http-connections`;
    - `PATCH`/`DELETE /assistants/{id}/http-connections/{http_connection_id}`;
    - `permissions.scope_to_asker` on database connections;
    - `warnings` on a connection test;
    - `internal_destinations` in organisation settings;
    - `kind: "http"` on `POST /assistants/{id}/actions:check`;
    - `tools.http_request.actions` in the config;
    - an `http_connection` drift kind.
  - Migration `c9a3b4c5d6e7`.
- **The approvals inbox, emails that only link, and My requests**
  (EXPLAINER §19, 9.2). Each request has its own page, where emails link;
  Approvals shows what waits for you, what you decided and, for admins,
  every request in the organisation; everyone has **My requests** in
  Chat. The sidebar shows how many wait for you. A burst of requests is
  one email per approver. For API clients: `GET /approvals/{id}`,
  `/approvals/mine`, `/approvals/decided`, `/approvals/summary`, and
  `GET /orgs/{org}/approvals` (admins, with `status` and `assistant_id`).
  Migration `c9a2b3c4d5e6`.
- **Declared actions, and requests that wait safely** (EXPLAINER §19,
  9.1). A database node can carry named statements the builder writes,
  with values the model fills and the asker's own address, name and id
  filled by the platform; each runs at once (a read), once the asker
  confirms (only a statement pinned to their own rows), or once an
  approver decides. Any write that needs a person is stored with its
  exact input sealed, and the answer goes on; the asker or an approver
  decides whenever they can, and it runs once, after everything is
  checked again, with the outcome posted back and the asker emailed.
  Each assistant can name its approvers (groups or people; the admins
  when nobody is named); approvers can hand their requests to someone
  while away. A new **Approvals** queue for whoever may decide. For API
  clients: `POST /approvals/{id}:resolve` takes `input_sha256` (required
  when the request has one), `note` and `params`; `GET
/conversations/{id}/approvals?all=true`; `POST /approvals/{id}:withdraw`;
  `GET /approvals/decidable`; `GET`/`PUT /assistants/{id}/approvers`;
  `GET`/`POST /approvals/delegations`, `DELETE
/approvals/delegations/{id}`; `POST /assistants/{id}/actions:check`.
  `GET /orgs/{org}/approvals` is gone. Migration `c9a1b2c3d4e5`.
- **Browser tests for the employee flows** (EXPLAINER §18, 8.11). A
  Playwright suite (`npm run e2e -w web`) on its own throwaway stack (an
  API on 8020 with SQLite and the fake driver, the web app on 3020): an
  employee never sees the Studio, the privacy notice comes first, links
  survive signing in, a dropped connection picks the answer up, phones sign
  in and chat, removing is honest and undoable. In `scripts/check.ps1` and
  a CI job. New dev dependency `@playwright/test` 1.62.1.
- **One purge path, retention and legal holds** (EXPLAINER §18, 8.10).
  Conversations are erased for good 365 days after their last message (an
  admin can change it; below a year the page warns about DPDP), every
  night, in small batches, with their memory notes, CLI transcript and
  session key. The owner can erase one person's data and put people or
  conversations on a legal hold that every purge skips. Each erasure is
  recorded, and `restore.sh` saves the record so it can be applied again
  after a restore. Traces carry metadata only in production
  (`TRACE_CONTENT`). For API clients: `PUT /orgs/{org}/retention`, `POST
/orgs/{org}/members/{user_id}:erase`, `GET`/`POST /orgs/{org}/holds`,
  `POST /orgs/{org}/holds:release`, `GET /orgs/{org}/erasures`;
  `retention_days` in organisation settings and the notice. Migration
  `c8a9b4c5d6e7`.
- **Per-person caps, the provider's limit, and alerts** (EXPLAINER §18,
  8.9). Spend is recorded per person. Admins set a daily and monthly limit
  for each Employee and each builder, and their own for one person;
  reaching it stops only that person, who is told without money at 80% and
  100%. Employees get a smaller message rate. The model provider's spending
  limit is now its own failure instead of "try again in a minute". When
  something stops Chat for everyone, admins get an email (at most hourly),
  a banner and an Alerts page. For API clients: `GET`/`PUT
/orgs/{org}/budgets/people`, `PUT /orgs/{org}/members/{user_id}/budget`,
  `GET /orgs/{org}/alerts`, `POST /orgs/{org}/alerts:resolve`; the
  `person_limit` and `provider_spend_cap` failure codes; budget events with
  `scope: "person"`; `RATE_LIMIT_CHAT_EMPLOYEE`. Migration `c8a8a3b4c5d6`.
- **History and feedback** (EXPLAINER §18, 8.8). Removing a conversation
  now says what it does: out of your lists and search, kept for the
  company, admins can still open it, and **Removed** puts it back.
  Search your conversations across assistants. A thumbs (with an optional
  comment) on each answer, and "Did this solve it?" once per conversation.
  Builders get a Feedback tab: thumbs by version, the solved rate with how
  many answered, and comments with their question and answer, never who
  asked. For API clients: `POST /conversations/{id}:restore`, `PUT
/conversations/{id}/messages/{message_id}/feedback`, `PUT
/conversations/{id}/outcome`, `GET /assistants/{id}/feedback`; `GET
/chat/conversations` takes `q` and `removed`; a conversation's detail
  carries `feedback` and `solved`. Migration `c8a7f2a3b4c5`.
- **How Chat uses your data** (EXPLAINER §18, 8.7). Before their first
  message in Chat, everyone reads and accepts a notice: what is kept,
  that admins can open conversations, the rules, where messages go (from
  the server's setup) and who to ask. The owner edits it and chooses
  whether a change asks everyone again; every text and acceptance is kept.
  **Privacy & help** shows each time an admin opened one of your
  conversations, and why. Each assistant has a card (**About**): what its
  approved version uses, can do and sends out, and what it remembers about
  you. New `docs/DATA_FLOWS.md`. For API clients: a live conversation's
  message is refused with `notice_required` until the notice is accepted;
  `GET /chat/notice`, `POST /chat/notice:accept`, `GET /chat/access`, `GET
/chat/assistants/{id}/about`, `GET`/`PUT /orgs/{org}/notice` and
  `/notice/revisions`; the `privacy_contact` setting;
  `PRIVACY_NOTICE_REQUIRED` (on). Migration `c8a6e1f2a3b4`.
- **The Chat app** (EXPLAINER §18, 8.6). Answers survive a dropped
  connection: keep-alives while a turn is quiet, and the app picks the
  answer up from the last event it saw. A message the server never kept
  goes back in the box, **Try again** waits when the server says how long,
  and employees read failures without money or settings. Links come back
  after sign-in, conversations have a stable link (`/chat/c/{id}`), and a
  withdrawn assistant leaves its conversations readable. Employees get
  their own slim shell. Sign-in works on phones, Chat installs to the home
  screen with an offline page, and an iPhone app signs in with a code from
  another device. For API clients: an `accepted` stream event; `busy` is
  retryable with `retry_after_s`; `budget_exceeded` carries `resets_at`;
  `POST /auth/device-code` and `/auth/device-code/redeem`; `GET
/chat/help`; the `offline_help` setting; the `X-Turn-Id` header is
  readable across origins.
- **Audiences, groups and the catalog** (EXPLAINER §18, 8.5). Admins make
  groups; builders set each assistant's audience (everyone, groups or
  named people), category and conversation starters on the new Listing
  tab, frozen into the next version and reviewed with it. Chat shows each
  person only what they are in (admins and an assistant's builders see
  it always); Official assistants come first; search and categories. For
  API clients: `/orgs/{org}/groups` and its members; `GET`/`PUT
/assistants/{id}/listing`; `POST /assistants/{id}:official`;
  `GET /chat/assistants` takes `q` and `category`; versions carry
  `listing`. Every existing version is for everyone. Migration
  `c8a5d1e2f3a4`.
- **Two-step sign-in** (EXPLAINER §18, 8.4). An authenticator app, with ten
  recovery codes, set up in Your account. With `REQUIRE_ADMIN_2FA` (on in
  the production compose), the owner and admins act as admins only after a
  second factor: a code, or a Google sign-in limited to the company's
  domain. `python -m scripts.reset_two_factor` turns it off for someone
  locked out. Sign-in limits are now per person, sign-up has its own
  bucket, and `TRUSTED_INTERNAL_NETS` raises per-address ceilings for
  office and VPN ranges. For API clients: `POST /auth/login` returns
  `LoginOut` (tokens, or `two_factor_challenge`), finished at
  `POST /auth/login/two-factor`; `/auth/two-factor` and its `setup`,
  `confirm`, `verify`, `recovery-codes` and `disable`; `/auth/me` gains
  `two_factor`. Migration `c8a4c1d2e3f4`.
- **Google sign-in** (EXPLAINER §18, 8.3; OPERATIONS §3). Off until
  `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and (recommended)
  `GOOGLE_ALLOWED_DOMAIN` are set. Accounts are found by Google's id, never
  by address: an existing account connects Google from Your account; new
  ones come through an invite or the owner's opt-in auto-join (as
  Employees), and have no password. For API clients: `GET /auth/providers`,
  `GET /auth/google/start`, `GET /auth/google/callback`,
  `POST /auth/google/exchange`, `POST /auth/google/link-ticket`,
  `DELETE /auth/google`; `/auth/me` gains `sign_in`; a new
  `google_auto_join` organisation switch. Migration `c8a3b1c2d3e4`
  (`user_identities`; `users.password_hash` may be empty).
- **An internal host** (EXPLAINER §18, 8.2; OPERATIONS §2). The proxy can
  get its certificate through DNS (`TLS_MODE=acmedns` or `cloudflare`,
  with the proxy image built from `deploy/proxy/caddy.Dockerfile`), so the
  server needs no port open to the internet, and listens on one address
  (`BIND_IP`). The preflight warns about a production server set up to
  face the internet, or whose name resolves to a public address. New
  settings: `TLS_MODE` (default `public`, as before), `BIND_IP`,
  `CLOUDFLARE_API_TOKEN`, `ACMEDNS_USERNAME`, `ACMEDNS_PASSWORD`,
  `ACMEDNS_SUBDOMAIN`, `ACMEDNS_SERVER_URL`.
- **The Employee role** (EXPLAINER §18, 8.1). People who only chat: they
  see the assistants that answer in Chat and their own conversations, and
  receive answers without the tool inputs and outputs, model, tokens or
  cost. Their requests that need approval wait for an admin, who is
  emailed and finds them on a new **Approvals** page. **Invites now make
  employees by default**; choose Builder for people who build. For API
  clients: role `employee`; new `GET /chat/assistants`,
  `GET /chat/assistants/{id}`, `GET /chat/conversations` and
  `GET /orgs/{org}/approvals` (admins); `POST /approvals/{id}:resolve`
  answers 403 `admin_decides` to an employee; a new `approval_emails`
  organisation switch. Every other route refuses an employee with 403
  `studio_access_required`.
- **The threat model is rewritten for one company** (EXPLAINER §17,
  7b.10): the deployment it assumes, the insider admin and the other
  people who might attack, a wall for each Phase 7 change, what is still
  open, and what must hold before Chat opens to employees. Its list of
  who may call each route is written from the tested role matrix, and a
  test fails when they differ.
- **CI runs the integration tier** (EXPLAINER §17, 7b.9). A new
  `api-integration` job starts the compose datastores on every pull
  request, migrates Postgres, runs `alembic check` there and the
  Postgres, Redis, MySQL and Mongo tests; `api-retrieval` runs the
  local-model retrieval tests nightly and on `main`. A test that can't
  reach a service named in `REQUIRE_INTEGRATION` now fails instead of
  skipping (`scripts/check.ps1` requires Postgres and Redis). Phase 7's
  migrations are taken down and back up with data, on SQLite and Postgres.
- **Organisation settings** (EXPLAINER §17, 7b.8). The owner can rename
  the organisation and switch off review emails, decision emails, or the
  sole owner approving their own version. Each change is in the audit
  log. For API clients: `GET` and `PATCH /orgs/{org}/settings`;
  `POST /orgs` answers 403 `org_creation_closed` in single-org mode; an
  archived organisation answers 404 and leaves `GET /orgs` and
  `/auth/me`. Migration `c7b8a1b2c3d4`.

- **Email** (EXPLAINER §17, 7b.5). With `SMTP_HOST` and `MAIL_FROM` set,
  invites are emailed, admins hear when a version waits for review and
  builders when theirs is decided. Messages only notify, with a link into
  the app; plain text; no conversation content. Sending runs on its own
  queue with retries, in a new `mailer` container (prod and dev compose).
  New settings: `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`,
  `SMTP_USERNAME`, `SMTP_PASSWORD`, `MAIL_FROM`, `MAIL_FROM_NAME`,
  `MAIL_BACKEND`, `MAIL_MAX_TRIES`. Without them nothing is emailed.
- **An assistant answers in Chat only once an admin approves it**
  (EXPLAINER §17, 7b.1). Publish is now **Submit for review**: an admin or
  the owner approves or rejects each version, with a note, and can roll
  back and pause. For API clients:
  - `POST /assistants/{id}/versions` submits; it no longer goes live.
    Approve, reject, withdraw and roll back are
    `POST .../versions/{n}/approve|reject|withdraw|make-live`; pausing is
    `POST /assistants/{id}/archive` (with a reason) and `/unarchive`.
  - `PATCH /assistants/{id}` refuses `status` (422): the status follows
    the review.
  - Starting or posting in a conversation on an assistant with no
    approved version, or a paused one, is refused (409
    `assistant_unavailable`). Builders test drafts in a preview:
    `{"mode": "preview"}` (optionally `"version": n`) when creating a
    conversation; `?mode=preview` lists them.
  - A conversation stays on the version it started on.
  - On upgrade every existing version is approved (an audit row per
    assistant says so), and conversations that ran on a draft become
    previews.
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

- The Phase 8.5 audiences migration no longer fails on SQLite, where a
  version's id comes back as text.
- Loading saved erasures after a restore no longer inserts one twice when
  the file lists it twice (EXPLAINER §18, 8.10).
- Too many sign-ups from one address now says so ("Too many sign-ups from
  this address.").
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

- **Nobody decides their own request** (EXPLAINER §19, 9.1, plan D7): the
  person in the conversation used to approve their own write. Now the
  asker confirms only a declared action pinned to their own rows (or a
  builder's test write in a preview); everything else waits for an
  approver. A decision names the hash of what it was shown. The exact
  input is kept sealed under the KEK, never re-asked of the model, and
  wiped once the request ends.
- **A tamper-evident, complete audit log** (EXPLAINER §17, 7b.7). Entries
  form a hash chain, sealed each minute by the worker; the owner's
  **Check integrity** finds an edited, deleted or inserted entry and shows
  the newest hash to keep elsewhere. Sign-ins (and failed ones), sign-outs
  and the remaining unrecorded changes are now recorded, and a test fails
  when a new route records nothing. A new **Audit log** page for admins,
  with filters, and a CSV export for the owner. For API clients:
  `GET /orgs/{org}/audit-log` takes `action` (a prefix), `actor`,
  `target`, `since` and `until`, and entries carry `actor_label`, `ip`
  and `seq`; `GET .../audit-log.csv` and `.../audit-log/integrity` are
  the owner's. Migration `c7b7a2b3c4d5` adds the chain columns and lets
  the trigger allow the one-time seal.
- **Password reset, change and confirmed addresses** (EXPLAINER §17,
  7b.6). One-time links, stored hashed, with the token after `#` so it
  reaches no log; a reset form that doesn't reveal who has an account; a
  reset or change ends every session, older access tokens included, and
  emails a notice. Access tokens carry their issue time to the microsecond
  (`iat_us`), so one issued in the same second as a reset ends with it.
  `python -m scripts.reset_link` makes a link on the
  server when email can't. New settings: `PASSWORD_RESET_TTL_MINUTES`,
  `VERIFY_EMAIL_TTL_HOURS`.
- **Members can be removed** (EXPLAINER §17, 7b.4). Deactivating someone
  ends their access and signs them out everywhere, hands their assistants
  to someone who stays, closes their pending approvals and withdraws the
  invites they sent; reactivating brings back the person only. An
  organisation always keeps an owner who can sign in. Invites can be
  listed and withdrawn, one is open per address, and an invite stops
  working when its sender is demoted or removed. Signing up with an
  invite joins that organisation only. Assistants can have co-editors
  (`/assistants/{id}/editors`). New setting: `INVITE_TTL_DAYS`.
- **Nobody approves their own assistant** (EXPLAINER §17, 7b.3).
  Whoever submitted a version or built the assistant can't approve it
  while another active admin or the owner could (`cannot_approve_own`);
  the owner, with nobody else to ask, approves their own with a required
  note, recorded as self-approved. Tool-call approvals stay with the
  person in the conversation until Phase 9, but each decision now records
  who asked, who decided, their role and whether it was the same person.
- **Conversations are private** (EXPLAINER §17, 7b.2). Each person lists
  and reads only their own. Admins and the owner can open anyone's by
  giving a reason (the `X-Read-Reason` header; the web app asks for it),
  and each read is recorded in the audit log before anything is returned.
  Other people's titles are left out of lists and Usage, and a rename no
  longer stores titles in the audit log.
- **An approved version is frozen.** Its knowledge, database connections,
  MCP servers and tools are recorded when it is submitted. Chat keeps
  answering from the approved copy of a reindexed document; a connection,
  server or tool changed after approval is left out of the live version's
  answers until a new version is approved; approving or rolling back
  refuses while anything has drifted; a source the live version answers
  from can't be deleted. Each run records what it answered with
  (EXPLAINER §17, 7b.1).
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
