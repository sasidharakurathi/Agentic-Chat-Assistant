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
| `proxy` | TLS, and routing by path (Caddy) | the internet: ports 80, 443 |
| `web` | the pages (Next.js) | the proxy |
| `api` | the API, and the agent's turns | the proxy |
| `worker` | background jobs: indexing documents, eval runs, summaries, a daily sweep of the agent's files, and a recovery sweep every minute (approvals and questions a crashed turn left behind) | nothing |
| `mcp-runner` | runs local-command MCP servers, away from every secret | the API and worker |
| `postgres` | everything stored, including vectors (pgvector) | the API and worker |
| `redis` | the job queue, rate limits, cross-process signals, and each running answer's events for 10 minutes (so a page can watch it again through any API process) | the API and worker |
| `minio` | uploaded files | the API and worker; the proxy, for citation links |

Two things leave the server: calls to the model (Anthropic) and, unless
`RAG_OFFLINE=1`, to the embedding service (Voyage). Assistants that use
web search, URL sources, HTTP tools or remote MCP servers reach whatever
those point at. Nothing else calls out.

## 2. Install

You need a Linux machine with Docker and the Compose plugin, a DNS name
pointing at it, and ports 80 and 443 open.

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
| `REGISTRATION` | `invite` | the server is private and anyone on the network may sign up (`open`) |
| `AGENT_MAX_CONCURRENCY` | 8 | chats queue at busy times (raise it, with `API_MEMORY`); each running turn is a subprocess |
| `API_MEMORY`, `WORKER_MEMORY` | 4g | the API is killed for memory (raise), or the machine is small (lower, with concurrency) |
| `RAG_OFFLINE` | 0 | you have no Voyage key: the local model is free, slower, and less accurate |
| `METRICS_TOKEN` | empty | you run Prometheus (section 7) |
| `TRUSTED_PROXY_HOPS` | 1 | there is another proxy or load balancer in front of Caddy (2) |
| `MODEL_ALIASES` | none | a model family's newest model changes: repoint the alias ("haiku") and every assistant on it moves, with no new version |

The load test and what it says about concurrency and the database pool
are in `EXPLAINER.md` §12.5.

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
Files that are in the bucket and not in the backup are left alone.

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
5. If the domain changed, set `DOMAIN` and rebuild (`up -d --build`): the
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
    cannot be edited or deleted: a trigger refuses it.
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
- **Who did what:** the audit log, per organisation, for its admins:
  `GET /api/v1/orgs/<org id>/audit-log` (there is no page for it yet).
- **Spend:** the Usage page, per assistant, conversation and model.

## 9. When something is wrong

| What you see | Likely cause | What to do |
| --- | --- | --- |
| `up` stops with `required variable X is missing a value` | `.env.production` | fill `X` in |
| The API restarts in a loop | the preflight is failing | `COMPOSE logs api`, fix the `FAIL` lines |
| `FAIL encryption key: APP_KEK does not open the stored credentials` | the key is not the one the data was saved with | put the original key back (section 4) |
| The browser warns about the certificate | `DOMAIN` is `localhost`, or DNS / ports 80 and 443 are not reaching the server | fix DNS or the firewall, then `COMPOSE restart proxy` |
| Chat answers arrive all at once | a proxy in front is buffering | it must not buffer `/api/*` (see `deploy/proxy/Caddyfile`) |
| "Too many conversations are running right now" | all turn slots are busy | raise `AGENT_MAX_CONCURRENCY` (and memory), or wait |
| A document stays at "pending" | the worker is not running, or Redis is down | `COMPOSE ps worker redis`, `COMPOSE logs worker` |
| Everyone gets "Too many requests" | `TRUSTED_PROXY_HOPS` is 0 behind the proxy, so everyone shares one address | set it to the number of proxies in front |
| Sign-up says "by invitation" | `REGISTRATION=invite` | invite them from Members, or set `open` |
| Local-command MCP servers cannot download their package | the runner has no route out, on purpose | bake the server into the runner image, or add `docker-compose.mcp-egress.yml` (`EXPLAINER.md` §10.6) |

For anything else: the `request_id`, then `COMPOSE logs api | grep <id>`.

### A suspected incident

Report a cyber incident to CERT-In within **6 hours** of noticing it; from
14 May 2027, a personal-data breach also goes to the Data Protection Board
and to the people affected within 72 hours. Then:

1. **Keep the evidence.** Run `deploy/backup/backup.sh` at once, and copy
   the proxy's access log out of its volume:
   `COMPOSE cp proxy:/var/log/caddy ./incident-<date>/caddy`.
2. **Who did what, in the window.** In `COMPOSE exec postgres psql -U app app`:

   ```sql
   SELECT created_at, action, actor_user_id, ip, target_type, target_id, meta
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

## 10. Before you open it to people

- [ ] `APP_KEK` is stored somewhere other than the server.
- [ ] `REGISTRATION=invite`, unless open sign-up is what you want.
- [ ] A budget is set for the organisation.
- [ ] `backup.sh` and `verify.sh` run on a schedule, and the backups are
      copied off the machine.
- [ ] You have restored one backup, once, onto a test machine.
- [ ] The preflight shows no `WARN` you have not decided to accept.
- [ ] `CADDY_IMAGE` and `APP_VERSION` are pinned (the MinIO image is pinned by
  digest in the compose file; `MINIO_IMAGE` only if you tested another).
- [ ] `docs/THREAT_MODEL.md` §5 (what is still open) has been read.
