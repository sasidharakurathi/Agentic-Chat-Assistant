# Giving an assistant database access safely

*Task 3.10 — least-privilege guidance per engine.*

Assistant Studio enforces a lot at the application layer: every statement is
parsed and classified before it runs, reads get a `LIMIT`, writes and schema
changes stop for human approval, denied tables are hidden from introspection
entirely. **None of that is a substitute for a restricted database role.**

The reason is worth being blunt about. The query guard is software, and
software has bugs; a role grant is enforced by the database itself. If the
guard ever misses something, a read-only role turns a breach into an error
message. Treat the guard as the thing that gives *good error messages* and the
role as the thing that actually protects the data.

Two rules that make everything else easier:

1. **Never reuse an application account.** Your app's user can almost
   certainly write everywhere. Make a new role for the assistant.
2. **Point at a replica when you have one.** Read-only at the *server* level
   beats read-only at the grant level, and a runaway query cannot hurt
   production.

---

## PostgreSQL

```sql
-- A login role that owns nothing and can create nothing.
CREATE ROLE assistant_ro LOGIN PASSWORD 'use-a-generated-secret' NOSUPERUSER NOCREATEDB NOCREATEROLE;

REVOKE ALL ON DATABASE appdb FROM PUBLIC;
GRANT CONNECT ON DATABASE appdb TO assistant_ro;
GRANT USAGE ON SCHEMA public TO assistant_ro;

-- Read only, and only what exists today.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO assistant_ro;

-- Tables created later are NOT covered by the grant above; this fixes that.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO assistant_ro;

-- Keep genuinely sensitive tables out of reach entirely.
REVOKE ALL ON TABLE salaries, api_keys FROM assistant_ro;

-- Belt and braces: cap how long any one statement can run.
ALTER ROLE assistant_ro SET statement_timeout = '10s';
```

**If you need writes**, grant them on exactly the tables that need them, never
schema-wide:

```sql
GRANT INSERT, UPDATE ON TABLE support_tickets TO assistant_ro;
-- and still leave DELETE and DDL out unless you truly need them
```

> `ALTER DEFAULT PRIVILEGES` is the line people miss. Without it, a table
> created next month is invisible to the assistant, which looks like a bug in
> the product rather than a missing grant.

## MySQL / MariaDB

```sql
CREATE USER 'assistant_ro'@'%' IDENTIFIED BY 'use-a-generated-secret';

-- Database-wide read.
GRANT SELECT ON appdb.* TO 'assistant_ro'@'%';

-- MySQL has no REVOKE-on-one-table-after-a-wildcard-grant, so for sensitive
-- tables grant per table instead of using the wildcard above:
--   GRANT SELECT ON appdb.customers TO 'assistant_ro'@'%';
--   GRANT SELECT ON appdb.orders    TO 'assistant_ro'@'%';

FLUSH PRIVILEGES;
```

Set a server-side ceiling too, since `MAX_EXECUTION_TIME` only applies to
`SELECT`:

```sql
SET GLOBAL max_execution_time = 10000;  -- ms
```

## SQLite

There are no roles — access is the file. So:

- Point the connection at a **copy**, or at a file on a read-only mount.
- The adapter opens the file with `mode=ro` unless a write has been approved,
  which means even a guard failure cannot write through the handle.
- Keep the file outside any directory the web process can serve.

## MongoDB

```js
use admin
db.createUser({
  user: "assistant_ro",
  pwd: "use-a-generated-secret",
  roles: [{ role: "read", db: "appdb" }]   // NOT readWrite, NOT dbAdmin
})
```

v1 exposes only `mongo_find` and `mongo_aggregate`, and the aggregation
pipeline is screened for `$out`, `$merge`, `$function`, `$where` and
`$accumulator` — so writes and server-side JavaScript are unreachable through
the agent regardless of the role. The `read` role makes that true at the
server as well.

---

## What the product enforces on top

Set per connection, in **Databases → Permissions**:

| Setting | What it does |
|---|---|
| `read` / `write` / `ddl` | Statement types the guard will accept at all. `ddl` requires `write`. |
| `deny_tables` | Removed from query results **and from introspection** — the assistant never learns the table exists. |
| `allow_tables` | When set, an exhaustive list; everything else is refused. |
| `row_limit` | A `LIMIT` is injected into every read, and tightened if the model asked for more. |
| `statement_timeout_ms` | Enforced by the database: Postgres `statement_timeout` (inside the query's own transaction), SQLite's progress handler, MySQL `MAX_EXECUTION_TIME` backed by a client deadline + `KILL QUERY` (it covers only `SELECT`), Mongo `maxTimeMS`. The client gives up one second later as a backstop. |

And per assistant, on the canvas database node:

- **Expose writes** — a second gate. The connection's permission says what the
  *credential* may do; this says what *this assistant* may ask for. Both must
  agree, so pointing a second assistant at a writable connection does not
  silently hand it write access.

Whatever passes all of that, if it modifies data, still stops for a human with
the exact statement shown before it runs.

## Connections, caching and reachability

- **The schema cache follows the permissions.** It only ever holds what the
  profile allowed when it was built, so any change to permissions, host,
  database or credentials drops it, and a background refresh rebuilds it. An
  expired cache (`SCHEMA_CACHE_TTL_S`, default a day) is refreshed on the
  agent's next introspection.
- **Reachability is checked in the background.** Creating a connection queues
  a schema refresh, which connects, introspects, and records `ok` or `error`
  on the connection. Creation is not blocked on it: a database is often
  configured before its network path or role exists.
- **SQLite is not pooled, on purpose.** There is no server to keep a
  connection to; opening the file costs microseconds; and each query must be
  opened read-only or writable according to what was approved. A pooled
  handle would hold a file lock and one fixed mode.
- The in-app **Add connection** form shows the role to create for the chosen
  engine (it mirrors the snippets above).

## Credential storage

Passwords are sealed with AES-256-GCM under a per-secret data key, which is
itself wrapped with `APP_KEK` from the environment. The database stores only
ciphertext, so a dump without the KEK is inert; no endpoint can return a
password, and `DbConnectionSummary` has no field that could carry one.

Rotating `APP_KEK` re-wraps the small data keys rather than re-encrypting
every credential. To rotate, with the current key still in `APP_KEK`:

```sh
# from apps/api; NEW_APP_KEK comes from the environment, never the command line
NEW_APP_KEK=<new key> python -m scripts.rotate_kek --dry-run   # checks every secret
NEW_APP_KEK=<new key> python -m scripts.rotate_kek
```

then set `APP_KEK` to the new key and restart the API and workers (until then
they hold the old key). It runs in one transaction and proves each secret
against the new key before committing, so a failure changes nothing, and a
re-run skips secrets already on the new key. Each rotated row records
`rotated_at`. Neither key is ever printed.
