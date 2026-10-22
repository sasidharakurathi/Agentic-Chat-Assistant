# Operator guide

For the person who runs an Assistant Studio server: installing it, keeping
it backed up, upgrading it, and working out what is wrong when something
is. The builder's side (making assistants) is in
[`USER_GUIDE.md`](USER_GUIDE.md).

Every command here is run from the repo root on the server. `COMPOSE`
below stands for:

    docker compose --env-file .env.production -f docker-compose.prod.yml

## 1. What runs

| Container | What it does | Reachable from |
| --- | --- | --- |
| `proxy` | TLS, and routing by path (Caddy) | the office network and the VPN (an internal install, below); or the internet, ports 80 and 443 |
| `web` | the pages (Next.js) | the proxy |
| `api` | the API, and the agent's turns | the proxy |
| `worker` | background jobs: indexing documents, eval runs, summaries, a daily sweep of the agent's files and of document copies no version needs any more, and a recovery sweep every minute (approvals and questions a crashed turn left behind) | nothing |
| `mailer` | sends email from its own queue, with retries (Phase 7b.5) | nothing; reaches the mail server |
| `mcp-runner` | runs local-command MCP servers, away from every secret | the API and worker |
| `postgres` | everything stored, including vectors (pgvector) | the API and worker |
| `redis` | the job queue, rate limits, cross-process signals, and each running answer's events for 10 minutes (so a page can watch it again through any API process) | the API and worker |
| `minio` | uploaded files | the API and worker; the proxy, for citation links |

Two things leave the server: calls to the model (Anthropic) and, unless
`RAG_OFFLINE=1`, to the embedding service (Voyage). Assistants that use
web search, URL sources, HTTP tools or remote MCP servers reach whatever
those point at. Google sign-in, the mail server and Langfuse are reached
when you set them up. Nothing else calls out. `docs/DATA_FLOWS.md` has
each one, with what it receives and how to turn it off.

## 2. Install

You need a Linux machine with Docker and the Compose plugin, and a DNS
name for it. For a company's own use, set it up as an **internal host**
(below): reached on the office network and over Tailscale, never from the
internet. Otherwise the name's public DNS points at the server and ports
80 and 443 are open (`TLS_MODE=public`).

1. Get the code onto the server.
2. `cp deploy/production.env.example .env.production`, then fill it in.
   The file says how to generate each value. `chmod 600 .env.production`.
3. **Copy `APP_KEK` somewhere that is not this server.** See section 4.
4. Start it:

       COMPOSE up -d --build

   The first build takes several minutes.
5. Read the API's first lines: `COMPOSE logs api`. The preflight prints
   one line per check and ends with `ready to start`, or with the `FAIL`
   lines to fix. It does not print values.
6. Open `https://<DOMAIN>` and create the first account. With
   `REGISTRATION=invite` (the default here) that is the only account that
   can be created without an invite.
7. Invite the rest of the team from **Members**.

Costs: every assistant on the server runs on the one `ANTHROPIC_API_KEY`.
Set a budget before inviting people (Usage › Budgets): a budget stops
chats at 100% and warns from 80%.

To try the whole stack without a model key or spend, set
`AGENT_DRIVER=fake` and `RAG_OFFLINE=1`: answers are canned.

### The internal host (Phase 8.2)

The server is reached on the office network and over Tailscale (plan
D15), phones included, and is not reachable from the internet. Three
things make that work: a name that answers with the server's private
address, a certificate obtained through DNS, and a proxy that listens on
that address only.

**One name, everywhere.** Pick one name, say `assistant.company.com`, and
use it from the office, from home and on phones. It is baked into links,
emails and the web image (`DOMAIN`); a second name for phones would break
them.

**DNS: the name answers with the private address.**
- **On the office network:** the company's DNS (or the router's) answers
  `assistant.company.com` with the server's LAN address.
- **Over Tailscale:** in the Tailscale admin console, DNS › add a split
  DNS nameserver for `company.com` pointing at the office DNS server (on
  a subnet router's network), or, with Tailscale on the server itself,
  answer the name with its `100.x` address. Phones then resolve it too.
- **The public zone** holds only the certificate's record
  (`_acme-challenge`, below), never an A record for the server. A public
  name that points at a private address is dropped by many routers and
  firewalls (DNS rebinding protection) and looks like "the app is down".
- **Android "Private DNS"** (Settings › Network) skips the network's DNS:
  set it to Automatic, or the name won't resolve on the office Wi-Fi.

**The certificate, through DNS.** Caddy proves it owns the name by writing
one TXT record, so no port has to be open to the internet. Build the
proxy with the DNS plugins once (and monthly after, section 7):

    docker build -f deploy/proxy/caddy.Dockerfile -t assistant-studio-proxy:2.10 deploy/proxy

then in `.env.production` set `CADDY_IMAGE=assistant-studio-proxy:2.10`
and one of:
- **`TLS_MODE=acmedns`** (any DNS provider; recommended): register an
  account with an acme-dns server (`curl -X POST https://auth.acme-dns.io/register`,
  or run your own), put its `username`, `password`, `subdomain` and the
  server URL in the `ACMEDNS_*` settings, and add one record in the
  public zone: `_acme-challenge.assistant.company.com CNAME <fulldomain>`.
  Those credentials can change that one TXT record and nothing else.
- **`TLS_MODE=cloudflare`**: a Cloudflare API token with Zone › DNS ›
  Edit on that one zone only, in `CLOUDFLARE_API_TOKEN`.

Never a global or account-wide key: the credentials sit on the server, in
the proxy's environment alone. A certificate's name is public in the
certificate transparency logs; only the name, not the address.

**Listen on one address.** `BIND_IP` is the address the proxy listens on:
- **Tailscale as a subnet router** on the office network (advertising the
  server's LAN address, with SNAT off, section 8): `BIND_IP` is the
  server's LAN address, which both the office and Tailscale reach;
- **Tailscale on the server:** its `100.x` address, if only Tailscale
  users reach it; or leave `BIND_IP` empty and let the host firewall
  allow 80 and 443 only from the LAN and the `tailscale0` interface.

Port 80 only redirects to https on the LAN; nothing needs it from
outside.

**Check it.** The preflight reviews `TLS_MODE` and `BIND_IP`, and what the
name resolves to from the server: a public address is a warning. Then
open the site from each place people will use, before telling them:
office Wi-Fi, home Wi-Fi with Tailscale on, and a phone on mobile data
with Tailscale on (Android and iOS). Each must show the sign-in page with
no certificate warning. From home with Tailscale off, it must not load.

### The MinIO image

MinIO no longer publishes images (`minio/minio` is gone from Docker Hub)
and its code is archived. The stack uses Chainguard's build,
`cgr.dev/chainguard/minio`, which is rebuilt from source and free to
pull, pinned by digest in both compose files. A digest means nothing
changes until someone pulls a newer build, tests it and updates the digest
(`MINIO_IMAGE` in `.env.production` overrides it). The one image carries
`mc` and a shell, which the bucket job and the backup scripts use.

**Moving from `minio/minio`:** Chainguard's server runs as user 65532, not
root. A one-off `minio-owner` step runs before it and hands the data
volume over (`chown`), only if it is still root's, so an existing
install keeps its files. Nothing to do by hand.

Any S3-compatible store works with the API; only the bucket job, the
volume step and the backup scripts assume this image.

## 3. Settings worth knowing

All are in `.env.production`; `deploy/production.env.example` explains
each. The ones that change behaviour most:

| Setting | Default | Change it when |
| --- | --- | --- |
| `REGISTRATION` | `invite` | the server is private and anyone on the network may sign up (`open`, and only with `ORG_MODE=multi`) |
| `ORG_MODE` | `single` | the server hosts several organisations, each with its own owner (`multi`; then anyone signed in can create one) |
| `AGENT_MAX_CONCURRENCY` | 8 | chats queue at busy times (raise it, with `API_MEMORY`); each running turn is a subprocess |
| `API_MEMORY`, `WORKER_MEMORY` | 4g | the API is killed for memory (raise), or the machine is small (lower, with concurrency) |
| `RAG_OFFLINE` | 0 | you have no Voyage key: the local model is free, slower, and less accurate |
| `METRICS_TOKEN` | empty | you run Prometheus (section 7) |
| `TRUSTED_PROXY_HOPS` | 1 | there is another proxy or load balancer in front of Caddy (2) |
| `PASSWORD_RESET_TTL_MINUTES`, `VERIFY_EMAIL_TTL_HOURS` | 60, 48 | reset and address-check links should last longer or shorter |
| `INVITE_TTL_DAYS` | 7 | invite links should stay open longer or shorter (1 to 60) |
| `REQUIRE_ADMIN_2FA` | `true` | never in production: the owner and admins act as admins only after a second factor (Phase 8.4) |
| `TRUSTED_INTERNAL_NETS` | empty | many people reach the server from one office or VPN address: list those ranges (CIDRs), and per-address limits there are much higher; per-person limits still hold |
| `MODEL_ALIASES` | none | a model family's newest model changes: repoint the alias ("haiku") and every assistant on it moves, with no new version |
| `LANGFUSE_*` | unset | you want traces in Langfuse. Give only the owner access to it |
| `ACTION_EXECUTOR` | `queue` in production | never in production: approved requests run on the `mailer` worker, once each, and survive an API restart (Phase 9.1). `inline` runs them in the API process (development) |
| `TRACE_CONTENT` | `metadata` in production | you need prompts and answers in traces (`full`). That is a second copy of every conversation, outside the audited path and out of reach of erasure and retention (Phase 8.10); preflight warns |

The load test and what it says about concurrency and the database pool
are in `EXPLAINER.md` §12.5.

### Google sign-in (Phase 8.3)

Off until set up. People then sign in with their company Google account
(and Google's own checks, two-step verification included), alongside
passwords.

1. **In the company's Google Cloud project** (one inside the company's
   Google Workspace organisation): APIs & Services › OAuth consent
   screen, user type **Internal** (only the company's accounts can use it,
   and no 100-user test limit). Then Credentials › Create credentials ›
   OAuth client ID, type **Web application**, authorised redirect URI
   `https://<DOMAIN>/api/v1/auth/google/callback`. Google never needs to
   reach the server: the redirect goes through the person's browser, so
   an internal host works (it must be a real name over https, not an IP).
2. **In `.env.production`:** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
   and `GOOGLE_ALLOWED_DOMAIN` (the Workspace domain, `company.com`).
   Restart: `COMPOSE up -d`. The preflight checks both client values are
   there, and warns without the domain.
3. **Who may sign in with Google:**
   - someone whose account already has Google connected (Your account ›
     Connect Google, done signed in with their password);
   - someone with an invite to their address, who joins with Google from
     the invite link (their account then has no password);
   - with **auto-join** on (Members › Organisation, the owner's switch):
     any account of the allowed domain, as an Employee.
   A Google sign-in is never joined to an existing account by matching
   the address: the person signs in with their password and connects
   Google themselves.
4. **Not on Google Workspace** (Microsoft 365, Zoho): leave
   `GOOGLE_ALLOWED_DOMAIN` empty. Any Google account can then be used, but
   only with an invite to its address or a connection made signed in;
   auto-join stays off.

### Limits for each person, and alerts (Phase 8.9)

**Usage › Limits for each person**: a daily or monthly limit for each
Employee, another for each builder, admin and the owner, and a person's
own in place of their role's. Reaching it stops only that person. Set the
Employee one before inviting people: one script or loop otherwise uses up
the organisation's budget for everyone.

**The provider's own limit.** Anthropic has a monthly spend limit per
account tier, and a new account may start low. When it is reached every
answer stops until the 1st of the month. Before opening Chat: check the
tier and the limit in the Anthropic Console and raise them; consider
separate Console workspaces (with their own limits) for the Studio and
evals and for Chat; and invite people in groups rather than all at once.
The app reports it as "The model provider's spending limit has been
reached" and alerts the admins.

**Alerts.** When something stops Chat for everyone, admins and the owner
get an email (at most once an hour for each problem), a banner, and the
**Alerts** page. The next answer that works clears it, or **Mark handled**
does. Email must be set up (§3) for the emails; the banner works without.
The metric `assistant_studio_turns_rejected_total` counts turns turned away
as busy or over a budget.

### Requests that wait (Phase 9.1)

A write that needs a person is stored, sealed, and runs once someone
decides, on the `mailer` worker. The owner sets how long one waits and
when the approvers are reminded (Members › Organisation; 72 and 24 hours
by default). Each assistant's approvers are set in its History tab
(admins); people hand their requests over under Your account.

- **A request stuck "running"** (the worker died mid-run) is marked
  failed after ten minutes and never retried by itself: check the system
  it writes to before anyone asks again.
- **Nothing runs** after approvals: is the `mailer` service up? Approved
  requests nobody picked up are handed over again every couple of
  minutes once it is.
- **Emails to approvers** are batched (Phase 9.2): the first request at
  once, then one "N requests wait" email per 15 minutes. Each links to
  the app, never acts by itself.
- **Backups** hold the sealed input of requests still open; it opens only
  with `APP_KEK`, and a key rotation re-wraps it.

### Phase 9.5 to 10.5, for operators

- **Hand-off emails** need email set up (§3). The contact should be an
  admin, to open the conversation.
- **Screenshots** are stored in the object store under `attachments/`
  and served through the API, so the bucket needs no CORS setting. They
  go with their conversation; ones uploaded and never sent are deleted
  by the nightly sweep after a day.
- **The worker** now also runs `review_fixed_sweep` every 5 minutes, which
  sends the "fixed in version N" emails.
- **Builder testing budget**: Members › Organisation. When it runs out,
  previews and evals stop and Chat carries on.
- **Analytics CSV downloads** are audited (`analytics.export`).

### Knowledge groups and share links (Phase 9.4)

- **Restricted knowledge.** Builders set who can read each source
  (Sources › Who can read it). Deleting a group leaves the sources that
  named it readable only by their other groups, or nobody; the delete
  message lists them.
- **A source "Waiting for you"** holds credentials or identity numbers and
  isn't indexed. Its builder chooses groups or confirms everyone may read
  it; the confirmation is in the audit log (`data_source.sensitive_confirmed`).
- **Share links** stop when revoked, when the conversation is deleted or
  erased, and when the person who shared it is deactivated. Openings are
  in the audit log (`conversation.share_opened`).

### Company systems (Phase 9.3)

Assistants reach only public addresses unless the owner lists a system
under Members › Organisation › **Internal destinations**. Each entry
gives:

- a host: a name, a `.suffix` for every name under it, or an address;
- the address range it must resolve into;
- its ports;
- whether plain http is allowed.

Every change is in the audit log. This machine, link-local addresses
(cloud metadata) and the stack's own networks are refused whatever the
list says.

- **The stack's own networks.** `docker-compose.prod.yml` gives `edge`,
  `data` and `mcp` fixed ranges (`172.31.240.0/24`, `.241.0/24`,
  `.242.0/24`) and tells the API (`PLATFORM_NETWORKS`). If your office or
  VPN uses those, set `EDGE_SUBNET`, `DATA_SUBNET` and `MCP_SUBNET` in
  `.env.production`. Docker keeps a network's range once it exists, so
  after changing them run `docker compose -f docker-compose.prod.yml
  down` (volumes are kept), then `up -d`.
- **Docker's own address pools** (`172.17.0.0/16` upwards, then
  `192.168.x`) can overlap a company range. The symptom: an internal
  system can't be reached from the containers while the host reaches it
  fine. Set `default-address-pools` in Docker's `daemon.json` to a range
  your company doesn't use, then restart Docker.
- **Over Tailscale.** Tailnet addresses are `100.64.0.0/10`, which isn't
  public, so list the system with that range (or its own `/32`).
  Containers reach the tailnet through the host's Tailscale. On Docker
  Desktop, MagicDNS names may not resolve inside containers: use the
  address as the host, or the system's ordinary DNS name.
- **A Postgres connection scoped to the person asking** needs a role
  that row-level security binds: not a superuser, not `BYPASSRLS`, and
  not the owner of the tables (or the tables use `FORCE ROW LEVEL
  SECURITY`). Each table needs a policy such as `USING (email =
  current_setting('app.asker_email', true))`. **Test connection** says
  if the role is wrong and lists tables with no policy, which everyone
  sees in full.
- **HTTP connections** keep their headers sealed under `APP_KEK`, like
  database passwords. A call goes only to its connection's host and
  never follows a redirect. When an approved request runs, it is refused
  if the connection's address changed since it was asked.

### Keeping and erasing (Phase 8.10)

**Members › Keeping and erasing**:

- **How long conversations are kept**: 365 days after the last message by
  default. Admins change it; below a year the page warns about India's
  DPDP one-year floor for logs. The worker erases what is older every night
  at 02:30 UTC, 500 at a time.
- **Legal holds** (the owner): a person or a conversation kept out of every
  purge, for a dispute or an investigation.
- **Erase one person's data** (the owner): their conversations, memory
  notes and feedback comments. Their account and the audit log stay.
- **What was erased**: every purge, counts only.

Erased data is gone from the database at once, and from backups once they
age out (`KEEP`, 14 days by default).

### The privacy notice (Phase 8.7)

Everyone accepts **How Chat uses your data** before their first message
in Chat; the server refuses the message otherwise. Before inviting
anyone:

- read the built-in text in **Members › How Chat uses your data**, and
  make it yours (the owner edits it);
- set **Privacy contact** in organisation settings, or the owner's address
  is used;
- check the "Where your messages go" list it shows: it is read from this
  server's settings, so turning on Voyage, Langfuse or Google adds them.

A real change to the text: save it with **Ask everyone to accept it
again**, and each person reads it before their next message. A typo:
leave that off. Every saved text is kept, and the audit log records each
change and each acceptance. `PRIVACY_NOTICE_REQUIRED` stays on;
preflight warns if it is off in production.

### Feedback (Phase 8.8)

People give each answer a thumbs (with an optional comment) and say once
per conversation whether it solved their problem. Builders read it on the
assistant's **Feedback** tab without names; admins can open the
conversation, which is a recorded read. "Did this solve it?" is kept
without a name even after the conversation goes, for the analytics (10.2).
Removing a conversation only hides it from its owner: erasing comes with
the retention rules (8.10), so until then a request to erase someone's
data is handled on the server by hand.

### The Chat app on phones (Phase 8.6)

Employees can add Chat to a phone's home screen. Three things make that
work:

- **A trusted certificate on the one name** (the internal host, §2).
  Without it a phone won't install the app, and the service worker that
  shows the offline page won't run.
- **The offline steps.** Organisation settings, **When the app can't reach
  the server**: your steps for getting on the office network or VPN
  ("Open Tailscale and switch it on…"). The app keeps them from its last
  visit, so write them before people install it.
- **iPhones and Google.** A home-screen app on an iPhone keeps its own
  storage, and a Google sign-in can end up in Safari instead. Such a
  person signs in with a code: on a computer or in Safari, Your account ›
  **Make a code**, typed into the app within five minutes. Each code works
  once, and making one is in the audit log.

The service worker caches only `offline.html`. After changing that page,
bump `CACHE` in `apps/web/public/sw.js` so phones fetch the new one.

### Email

Without `SMTP_HOST`, nothing is emailed: invite links are copied by hand,
and nobody is told when a version waits for review. To send:

1. Use a mailbox the company controls: a Workspace account, or a
   send-only address. A personal gmail.com account works, but its app
   password opens the whole account, mail included, and changing that
   account's password revokes the app password, so all email stops.
2. For Gmail or Workspace: turn on 2-step verification for the account,
   create an app password, and set in `.env.production`:
   `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_SECURITY=starttls`,
   `SMTP_USERNAME` and `MAIL_FROM` to the address, `SMTP_PASSWORD` to the
   app password.
3. `COMPOSE up -d`, then **Members** › **Email** › **Send me a test**.

The settings are environment only: nothing in the app can change where
mail goes. Each message is recorded (who to, what kind, the outcome, never
the body) in `mail_messages`; failures show as the week's failed count on
the same page, and in the mailer's log (`COMPOSE logs mailer`).

## 4. The one thing you cannot lose: `APP_KEK`

Database passwords and MCP credentials are stored encrypted. The key is
`APP_KEK`, and it is **not** in the database or in any backup.

- Lose it, and every stored credential is unreadable. There is no
  recovery: each one has to be entered again.
- Restore a backup on a server with a different key, and the API refuses
  to start: the preflight tries the key against a stored credential and
  says `APP_KEK does not open the stored credentials`.

Keep a copy in a password manager or a secrets store, separately from the
backups. The same goes for the rest of `.env.production`, though those
values can be regenerated (`JWT_SECRET`: everyone signs in again; the
datastore passwords: set new ones in the datastores too).

To change the key on purpose:

    read -rs NEW_APP_KEK && export NEW_APP_KEK      # paste the new key; it is not shown or kept in history
    COMPOSE exec -e NEW_APP_KEK api python -m scripts.rotate_kek --dry-run
    COMPOSE stop api worker
    COMPOSE run --rm -e NEW_APP_KEK -e PREFLIGHT=0 api python -m scripts.rotate_kek

then put the new key in `.env.production` and `COMPOSE up -d`. Back up
first (section 5). `-e NEW_APP_KEK` without a value passes the variable
from your shell, so the key never appears on a command line.

## 5. Backups

Three scripts in `deploy/backup/`. They work on the running stack.

    deploy/backup/backup.sh                       # make one
    deploy/backup/verify.sh                       # prove the newest one restores
    deploy/backup/restore.sh backups/<folder>     # put one back

A backup is a folder, `backups/<UTC time>/`:

| File | What |
| --- | --- |
| `db.dump` | the database, one consistent snapshot (`pg_dump`, custom format) |
| `objects/` | every uploaded file, as files |
| `manifest.json` | when, which schema revision, how many files, and the dump's checksum |

A folder without `manifest.json` is a backup that did not finish; the
other two scripts refuse it.

**What is not in it:** `APP_KEK` and the rest of `.env.production`
(section 4), the TLS certificates (Caddy gets new ones), and Redis (a
queue and counters: jobs that were waiting are lost, nothing else).
Nor the `agentstate` volume, on purpose: it holds the Claude CLI's session
transcripts and working folders, a cache of what the database already
stores. A conversation whose transcript is missing (a new server, a lost
volume) carries on from its stored messages; the transcripts are removed
when their conversation is archived or its assistant deleted, and a daily
job in the worker clears anything left over.

**Schedule it.** For example, nightly, keeping two weeks, and proving each
one:

    15 2 * * *  cd /srv/assistant-studio && KEEP=14 deploy/backup/backup.sh && deploy/backup/verify.sh >> /var/log/assistant-studio-backup.log 2>&1

`verify.sh` restores the dump into a scratch database next to the live
one, checks the schema revision, the main tables and the vector index,
and drops the scratch database. It does not touch the live data. A backup
that has never been restored is a hope; this makes it a backup.

**Copy them off the server.** `backups/` on the same disk protects
against a bad upgrade, not against losing the machine. Sync the folder to
object storage or another host after each run.

Settings, from the environment: `BACKUP_DIR` (default `./backups`),
`KEEP` (how many to keep; unset keeps all), `COMPOSE` (to address a
different stack).

## 6. Restore

### Onto the same server (undo something)

    deploy/backup/restore.sh backups/2026-09-30T02-15-00Z

It asks you to type the database's name, then stops the API and worker,
replaces the database, copies the files back, and starts them again.
Files that are in the bucket and not in the backup are left alone. It
saves the record of erasures first and prints the command that erases
again what was erased after the backup (step 5 below): run it.

### Onto a new server (the old one is gone)

1. Install as in section 2, steps 1 and 2, using the **original**
   `.env.production`, or at least the original `APP_KEK`.
2. Start only the datastores:

       COMPOSE up -d postgres redis minio

3. Copy the backup folder over, then:

       deploy/backup/restore.sh backups/<folder>

4. `COMPOSE up -d --build`, then `COMPOSE logs api`. The preflight should
   end with `encryption key: opens the stored credentials` and
   `ready to start`. If the backup is from an older release, the API
   migrates the database to this release's schema as it starts.
5. **Erase again what was erased after the backup** (Phase 8.10).
   `restore.sh` saved the record of erasures to `erasures-<when>.jsonl` and
   printed the command; once the API is up, run it:

       COMPOSE exec -T api python -m scripts.reapply_erasures --load - < erasures-<when>.jsonl

   On a new server, copy that file over from the old one if you can; without
   it, data erased after the backup comes back.
6. If the domain changed, set `DOMAIN` and rebuild (`up -d --build`): the
   web image is built for its domain.

Everyone has to sign in again only if `JWT_SECRET` changed.

## 7. Upgrade

Migrations run by themselves when the API container starts, and they go
one way: there is no downgrade. So the way back from a bad upgrade is a
restore, which makes the backup the first step and not an optional one.

1. **Read the release notes** (`CHANGELOG.md`) for anything marked as
   needing action.
2. **Back up and prove it:**

       deploy/backup/backup.sh && deploy/backup/verify.sh

3. **Note what you are running**, to be able to go back:
   `grep APP_VERSION .env.production`, and
   `docker image ls assistant-studio-api`.
4. **Get the new code**, and set `APP_VERSION` in `.env.production` to
   the new release's name, so the new images get their own tag and the
   old ones are still there.
5. **Compare the env example**: `deploy/production.env.example` may have
   new settings. New required ones stop `up` with their name.
6. **Build and start:**

       COMPOSE up -d --build

7. **Check:**
   - `COMPOSE logs api` : preflight `ready to start`, then
     `alembic upgrade head`, then the server.
   - `COMPOSE ps` : `api` is healthy.
   - `COMPOSE exec api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/readyz').read().decode())"`
     : `"status":"ok"`.
   - Sign in, open an assistant, send one message.

### The upgrade to the approval gate (Phase 7b.1)

From this release an assistant answers in Chat only from a version an
admin approved. The upgrade carries over what was there: every version
already published counts as approved (one `assistant.version.grandfathered`
audit row per assistant says so), and a conversation that ran on a draft
becomes a builder's preview. Nothing to do by hand, but tell your builders:
**Publish** is now **Submit for review**, and someone with the admin role
approves each version in History. A document reindexed after approval is
kept beside the new copy while a version still answers from it, so the
database grows by the old copy until the next approval and the daily
sweep (03:45) removes it.

### The proxy image, monthly (internal hosts)

With `TLS_MODE=acmedns` or `cloudflare`, the proxy is an image you built
(section 2), and it does not update itself. Once a month, rebuild it and
restart the proxy, so Caddy's security fixes arrive:

    docker build --pull -f deploy/proxy/caddy.Dockerfile -t assistant-studio-proxy:2.10 deploy/proxy
    COMPOSE up -d proxy

### The upgrade to Phase 7b: rehearse it first

The 7b releases change who can chat with what (the approval gate, private
conversations), sort out the personal organisations made at sign-up, and
switch production to one organisation. Rehearse on a copy of your
database before upgrading the real one:

1. **Back up** (step 2 above), and build the new images without starting
   them: `COMPOSE build`.
2. **Restore the backup into a scratch database**, beside the real one:

       COMPOSE exec -T postgres createdb -U app rehearsal
       COMPOSE exec -T postgres pg_restore -U app -d rehearsal --no-owner < backups/<folder>/db.dump

3. **Upgrade the copy, with the new code, and read the report:**

       COMPOSE run --rm --no-deps --entrypoint sh api -c 'export DATABASE_URL="${DATABASE_URL%/app}/rehearsal"; python -m alembic upgrade head && python -m scripts.upgrade_report'

   The report lists each organisation with its owners, people,
   assistants and spend, then what needs a decision. Check that:
   - the organisation your people use reads `team`, with an owner who
     can sign in;
   - what was archived was empty;
   - each personal org it warns about is one you know about.
4. **Drop the copy:** `COMPOSE exec -T postgres dropdb -U app rehearsal`.
5. **Upgrade for real** (steps 6 and 7 above), then run the report on
   the real database: `COMPOSE exec api python -m scripts.upgrade_report`.
6. **Decide the personal orgs it lists:**
   - `python -m scripts.upgrade_report archive <org id> --reason "..."`
     sets one aside: nobody reaches it, nothing in it is deleted, and
     `unarchive <org id>` brings it back. It refuses when someone would
     be left with no organisation (`--force` to do it anyway);
   - `freeze <org id>` keeps it readable and stops all spend there (its
     limits become $0; raising them on its Usage page lifts it);
   - or leave it.
7. **Tell your people:** builders submit versions for review; admins
   approve them in History; each person's conversations are private; the
   owner names the organisation in Members › Organisation.

### Going back

If step 7 fails and the cause is not a setting:

1. `COMPOSE stop api worker web`
2. Put the old code back and set `APP_VERSION` to the old name.
3. `deploy/backup/restore.sh backups/<the folder from step 2>` (the new
   release may already have migrated the database, and the old code does
   not know the new schema).
4. `COMPOSE up -d`

What was written between the backup and the restore is lost, which is why
an upgrade is done at a quiet time.

## 8. Watching it

- **Is it up:** `GET /healthz` (the process) and `GET /readyz` (the
  database, Redis, storage, the agent CLI, the MCP runner, each with its
  own result). `readyz` answers 503 only when the server cannot serve a
  chat at all.
- **Logs:** JSON lines on each container's output (`COMPOSE logs -f api`),
  capped at 50 MB per container and gone when a container is recreated,
  so days at most. Every API request line has a `request_id` (also the
  `x-request-id` response header: ask a user for it and search for it),
  the signed-in `user_id` and the `client_ip`. Secrets are redacted before
  a line is written.
- **What is kept for half a year or more** (Phase 7a.8, for CERT-In's 180
  days):
  - the proxy's access log, on the `caddylogs` volume
    (`/var/log/caddy/access.log`), rolled at 100 MB and kept 4392 hours:
    who connected from where and to what, with invite tokens, file-link
    signatures and Authorization values taken out;
  - the audit log in Postgres (who did what), which is in the backups and
    cannot be edited or deleted: a trigger refuses it. Nothing in it
    expires.
- **Behind a VPN or office NAT:** every remote user arrives from one
  address. That puts one IP in the logs and the audit log, and shares the
  per-address limits. On a Tailscale subnet router, turn off SNAT
  (`--snat-subnet-routes=false`) or run Tailscale on the server itself,
  then check that the audit log shows people's own addresses. Keep the VPN's
  own connection logs as the company's policy says.
- **Metrics, dashboards, alerts:** set `METRICS_TOKEN`, then see
  `EXPLAINER.md` §12.4. The proxy never publishes `/metrics`; Prometheus
  has to reach `api:8000` from inside (attach it to the stack's `edge`
  network).
- **Who did what:** the **Audit log** page, per organisation, for its
  admins (`GET /api/v1/orgs/<org id>/audit-log`). The owner can export it
  as CSV.
- **That nobody changed it** (Phase 7b.7): the worker seals new entries
  into a hash chain every minute, so the worker must run. Once a month,
  the owner presses **Check integrity** on the Audit log page and copies
  the newest fingerprint somewhere off the server (a password manager, a
  signed email to yourself). If a later check says an entry no longer
  matches, or the chain no longer reaches a fingerprint you kept, treat it
  as an incident (§9): someone with database access changed the record.
- **Spend:** the Usage page, per assistant, conversation and model.

## 9. When something is wrong

| What you see | Likely cause | What to do |
| --- | --- | --- |
| `up` stops with `required variable X is missing a value` | `.env.production` | fill `X` in |
| The API restarts in a loop | the preflight is failing | `COMPOSE logs api`, fix the `FAIL` lines |
| `FAIL encryption key: APP_KEK does not open the stored credentials` | the key is not the one the data was saved with | put the original key back (section 4) |
| The browser warns about the certificate | `DOMAIN` is `localhost` (`TLS_MODE=local`); with `TLS_MODE=public`, DNS or ports 80 and 443 not reaching the server; with a DNS mode, the credentials or the `_acme-challenge` record | `COMPOSE logs proxy`, fix what it names, then `COMPOSE restart proxy` |
| Chat answers arrive all at once | a proxy in front is buffering | it must not buffer `/api/*` (see `deploy/proxy/Caddyfile`) |
| "Too many conversations are running right now" | all turn slots are busy | raise `AGENT_MAX_CONCURRENCY` (and memory), or wait |
| A document stays at "pending" | the worker is not running, or Redis is down | `COMPOSE ps worker redis`, `COMPOSE logs worker` |
| Everyone gets "Too many requests" | `TRUSTED_PROXY_HOPS` is 0 behind the proxy, so everyone shares one address | set it to the number of proxies in front |
| Sign-up says "by invitation" | `REGISTRATION=invite` | invite them from Members, or set `open` |
| Local-command MCP servers cannot download their package | the runner has no route out, on purpose | bake the server into the runner image, or add `docker-compose.mcp-egress.yml` (`EXPLAINER.md` §10.6) |

For anything else: the `request_id`, then `COMPOSE logs api | grep <id>`.

### Someone can't reach it

Usually the name, not the server. Ask what they see:

| They see | Likely | Do |
| --- | --- | --- |
| "Site can't be reached" / "server IP address could not be found" | the name doesn't resolve where they are | at home: is Tailscale on? On Android: Private DNS set to Automatic? On the office Wi-Fi: does the office DNS answer the name? |
| It loads in the office, not at home | Tailscale's split DNS isn't set, or their device isn't on the tailnet | the Tailscale admin console, DNS and Machines |
| A certificate warning | `TLS_MODE=local`, or the certificate couldn't be renewed | `COMPOSE logs proxy`: the DNS credentials, or the `_acme-challenge` CNAME |
| It loads, but sign-in says "can't reach the server" | the page is from one name and the API another | `DOMAIN` is the one name, and the web image was rebuilt after it changed |
| The home-screen app says "Can't reach the assistant" | the app's offline page: the phone can't reach the server | as for "can't be reached" above; the steps in organisation settings are what it shows them |

From the server, `COMPOSE logs api | grep "site name"` shows what the name
resolved to when the API last started.

### Someone can't sign in

A forgotten password is reset from **Forgot your password?** on the
sign-in page, by email. When the server sends no email, or the owner is
the one locked out, make a link on the server and give it to them on a
channel you trust:

    COMPOSE exec api python -m scripts.reset_link someone@company.example

It works once, for `PASSWORD_RESET_TTL_MINUTES` (60), replaces any link
already sent, and issuing it is recorded in the audit log.

**Lost the phone and the recovery codes** (two-step sign-in, Phase 8.4):
check who is asking, in person or on a channel you trust, then

    COMPOSE exec api python -m scripts.reset_two_factor someone@company.example

It turns their two-step sign-in off, recorded in the audit log. They sign
in with their password and set it up again. This is the one way around
the second factor: keep it to the operator.

**Google sign-in leaves the iPhone app signed out** (Phase 8.6): they
sign in with a code instead. In Safari or on a computer, Your account ›
**Make a code**, then type it into the app's sign-in page. Nobody else
should ever ask for that code.

### Someone leaves

Remove them in the app (**Members** › **Remove**): their access and
sessions end, their assistants pass to whoever you choose, their pending
approvals close and their unused invites are withdrawn. Rotate the
database and MCP credentials the dialog lists. Then do the same wherever
else they had access: the company's Google account, the VPN or
Tailscale, and this server.

### A suspected incident

Report a cyber incident to CERT-In within **6 hours** of noticing it; from
14 May 2027, a personal-data breach also goes to the Data Protection Board
and to the people affected within 72 hours. Then:

1. **Keep the evidence.** Run `deploy/backup/backup.sh` at once, and copy
   the proxy's access log out of its volume:
   `COMPOSE cp proxy:/var/log/caddy ./incident-<date>/caddy`.
2. **Who did what, in the window.** First, as the owner, **Check
   integrity** on the Audit log page, and compare the fingerprint with
   the last one you kept. Then **Export CSV**, or in
   `COMPOSE exec postgres psql -U app app`:

   ```sql
   SELECT created_at, seq, action, actor_label, ip, target_type, target_id, meta
   FROM audit_log
   WHERE created_at BETWEEN '2027-01-10 00:00+05:30' AND '2027-01-11 00:00+05:30'
   ORDER BY created_at;
   ```

3. **Who asked an assistant anything:**

   ```sql
   SELECT DISTINCT u.email, c.id AS conversation, min(m.created_at) AS first, max(m.created_at) AS last
   FROM messages m
   JOIN conversations c ON c.id = m.conversation_id
   LEFT JOIN users u ON u.id = c.created_by
   WHERE c.assistant_id = '<assistant id>' AND m.role = 'user'
     AND m.created_at BETWEEN '<from>' AND '<to>'
   GROUP BY u.email, c.id;
   ```

4. **Who was shown a document** (an answer citing a knowledge source):

   ```sql
   SELECT DISTINCT u.email, m.conversation_id, m.created_at
   FROM messages m
   JOIN conversations c ON c.id = m.conversation_id
   LEFT JOIN users u ON u.id = c.created_by
   WHERE m.blocks @> '[{"type": "citation", "data_source_id": "<source id>"}]'
     AND m.created_at BETWEEN '<from>' AND '<to>';
   ```

5. **Who used a tool** (a database query, an MCP tool):

   ```sql
   SELECT t.created_at, u.email, t.tool_name, t.status, t.conversation_id
   FROM tool_calls t
   JOIN conversations c ON c.id = t.conversation_id
   LEFT JOIN users u ON u.id = c.created_by
   WHERE t.tool_name LIKE '%<tool>%' AND t.created_at BETWEEN '<from>' AND '<to>'
   ORDER BY t.created_at;
   ```

6. **Where from:** the proxy's access log for the same window
   (`remote_ip`, `request.uri`, `ts`), and the VPN's own logs.

## Browser tests (for whoever changes the code)

`npm run e2e -w web` runs the employee flows in Chromium (Phase 8.11). It
starts its own API on port 8020 (a fresh SQLite file, the fake driver: it
spends nothing and never reads the dev database) and its own web app on
3020, so those two ports must be free. It uses the Chromium already in
`%LOCALAPPDATA%\ms-playwright`; on a machine without one,
`npx playwright install chromium` in `apps/web` fetches it (ask before
downloading on a shared machine). A failed test leaves a screenshot and a
trace in `apps/web/test-results`.

## 10. Before you open it to people

- [ ] `APP_KEK` is stored somewhere other than the server.
- [ ] `REGISTRATION=invite`, unless open sign-up is what you want.
- [ ] A budget is set for the organisation, and a limit for each Employee.
- [ ] The Anthropic account's tier and monthly spend limit are checked and
      raised for the pilot (§3, "Limits for each person, and alerts").
- [ ] `backup.sh` and `verify.sh` run on a schedule, and the backups are
      copied off the machine.
- [ ] You have restored one backup, once, onto a test machine.
- [ ] The preflight shows no `WARN` you have not decided to accept.
- [ ] `CADDY_IMAGE` and `APP_VERSION` are pinned (the MinIO image is pinned by
  digest in the compose file; `MINIO_IMAGE` only if you tested another).
- [ ] `docs/THREAT_MODEL.md` §6 (what is still open) has been read, and
      §7 says whether employees may use Chat yet.
- [ ] The privacy notice is your own text, the privacy contact is set, and
      `docs/DATA_FLOWS.md` has been checked against the providers' current
      terms (Voyage's training opt-out above all).
