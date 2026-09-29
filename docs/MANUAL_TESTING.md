# Manual testing: Phases 0–4

Phase 4 so far covers tasks 4.1–4.7 (§10).

A hands-on walkthrough of everything built so far, with the test data to do
it. Work through it top to bottom the first time; later, each section stands
on its own.

Every chat step runs on the **fake driver** (`AGENT_DRIVER=fake`), which is
free: it calls the real tools (calculator, knowledge-base search, SQL) but
writes its answers from fixed patterns rather than a model. So the chat
steps below use exact trigger phrases. That tests the whole platform (tools,
permissions, approvals, citations, persistence) but not the quality of a
real model's answers. §9 covers the optional real-model pass, which costs
money.

Each test lists **Do** and **Expect**. Tick the box when it matches. If it
doesn't, note the step number and what you saw.

---

## 0. Setup

### 0.1 Start everything

In four PowerShell windows, from the repo root:

```bash
docker compose up -d postgres redis minio
```

```bash
docker compose --profile datastores up -d mysql mongo
```

Then one command per window:

```bash
.\scripts\dev-api.ps1
```

```bash
.\scripts\dev-web.ps1
```

```bash
.\scripts\dev-worker.ps1
```

The worker matters: without it, uploaded sources stay at **pending** forever.

### 0.2 Load the test data

```bash
.\scripts\seed-testdata.ps1
```

Expect five lines: `docs`, `sqlite`, `postgres`, `mysql`, `mongodb`. An engine
that isn't running prints `skipped` instead; start it and run the script
again. The script is safe to re-run at any time, and **re-running it is how you
reset the shop data** after the write tests in §6.

### 0.3 What the test data is

**Documents** (`testdata/manual/docs/`):

| File | Format | What it tests |
|---|---|---|
| `refund-policy.md` | Markdown with headings | Headings in citations (breadcrumb) |
| `warranty-terms.html` | HTML | HTML parsing, lists |
| `store-hours.txt` | Plain text | The simplest source |
| `shipping-faq.docx` | Word, with headings | DOCX parsing |
| `employee-handbook.pdf` | 4-page PDF | Page numbers in citations |
| `partner-notes-injection.md` | Markdown containing a prompt-injection attempt | Retrieved text is data, not instructions (§9, real model) |
| `server-log-no-spaces.txt` | 93 KB, no spaces at all | The chunker edge case fixed in 2.3 |
| `service-agreement-large.txt` | 233 KB | Indexing progress and reuse on reindex |

**Shop database**, the same rows in Postgres (`testshop`), MySQL (`appdb`) and
SQLite (`testdata/manual/shop.sqlite`):

| Table | Rows | Notes |
|---|---|---|
| `customers` | 8 | Asha Rao (Hyderabad) … Hiro Tanaka (Osaka) |
| `products` | 8 | 27-inch Monitor is the priciest at 249.99; USB-C Hub has stock 0 |
| `orders` | 15 | Statuses: delivered, shipped, processing, cancelled, refunded |
| `support_tickets` | 6 | 3 open with priority high/medium/low; ticket 1 is "Monitor arrived with a dead pixel" |
| `employee_salaries` | 4 | **Sensitive**: the table you deny-list |

**Mongo** (`testshop`): `orders` (15, with embedded customer and items),
`reviews` (5), and `staff_notes` (sensitive, deny-list it).

**Postgres role** `testshop_ro` / `testshop_ro`: can read the four shop tables,
can't write anything, and can't read `employee_salaries`. It's used in §6.8.

### 0.4 Accounts

Use a separate browser profile (or an incognito window) per person, so
sessions don't mix. Passwords are yours to choose; `TestPass123!` is fine for
all of them (8+ characters).

Registering creates a personal org named after the Name you enter, so
register the owner with the Name **QA Owner**. "QA Owner's Org" is the org
everyone else joins.

| Who | Name / email | Role in "QA Owner's Org" |
|---|---|---|
| Owner | QA Owner / `qa-owner@example.com` | owner (created at registration) |
| Admin | QA Admin / `qa-admin@example.com` | admin (invited) |
| Member | QA Member / `qa-member@example.com` | member (invited) |
| Outsider | QA Outsider / `qa-outsider@example.com` | none (their own org only) |

### 0.5 Fake-driver phrasebook

| Type this | What happens |
|---|---|
| anything else | Echo: `(fake driver) you said: …` |
| `calculate 21 + 21` | Calls the calculator tool, answers `42` |
| `search: how long do refunds take?` | Calls `kb_search` (knowledge base must be on) |
| `search twice: refunds` | Two `kb_search` calls in one turn |
| `sql: <one SQL statement>` | Runs `sql_query` on the **first** database wired to the assistant |
| `http: [METHOD] <url> [body]` | Calls `http_request` (the tool must be on; §10.1) |
| `mcp: <server>.<tool> {json}` | Calls an allowed MCP server tool (§10.7) |

Knowledge-base questions should read like good search queries. The fake
driver strips the `search:` / `search twice:` prefix and sends the rest as
the query, so write the rest as a question ("how long do refunds take?"),
not as an instruction ("look in the knowledge base please").

### 0.6 Where to look besides the app

| What | Where |
|---|---|
| API docs | http://localhost:8000/docs |
| Health | http://localhost:8000/healthz, http://localhost:8000/readyz |
| Uploaded files (MinIO) | http://localhost:9001, `minioadmin` / `minioadmin`, bucket `assistant-uploads` |
| The database | `docker exec -it assistant-studio-postgres-1 psql -U app -d app` |

For API-only checks, log in once in a PowerShell window and reuse `$h`:

```powershell
$api = "http://localhost:8000/api/v1"
$t = Invoke-RestMethod -Method Post "$api/auth/login" -ContentType "application/json" -Body (@{ email = "qa-owner@example.com"; password = "TestPass123!" } | ConvertTo-Json)
$h = @{ Authorization = "Bearer $($t.access_token)" }
$org = (Invoke-RestMethod "$api/orgs" -Headers $h).items | Where-Object name -eq "QA Owner's Org" | Select-Object -ExpandProperty id
```

---

## 1. Health and readiness (0.3)

- [ ] **1.1** Open `/healthz`. **Expect:** `{"status":"ok","version":...}`.
- [ ] **1.2** Open `/readyz`. **Expect:** `status: ok`, with `database`,
  `redis` and `storage` all `ok`, and `agent_cli: "skipped: offline driver"`.
- [ ] **1.3** Run `docker stop assistant-studio-minio-1`, then reload `/readyz`.
  **Expect:** HTTP 200, `status: degraded`, `storage: "error: timed out"`,
  answered in about 2 s. Run `docker start assistant-studio-minio-1`; within a
  few seconds `/readyz` is `ok` again.
- [ ] **1.4** Run `docker stop assistant-studio-postgres-1` and reload.
  **Expect:** HTTP **503**, `status: unavailable`. Start it again.

---

## 2. Accounts, orgs and roles (Phase 0)

- [ ] **2.1 Register.** Go to http://localhost:3000/register and register the
  owner with the Name **QA Owner**. **Expect:** you land on an empty
  Assistants page, signed in, with **QA Owner's Org** in the sidebar.
- [ ] **2.2 Sign out and back in.** Use the sidebar's **Sign out**, then log in.
  **Expect:** back on Assistants.
- [ ] **2.3 Return address.** Signed out, open
  `http://localhost:3000/login?next=/members` and log in.
  **Expect:** you land on Members.
- [ ] **2.4 Open-redirect guard.** Signed out, open
  `http://localhost:3000/login?next=//evil.example` and log in.
  **Expect:** you land on Assistants, not on another site.
- [ ] **2.5 A second org (API; there's no UI for it yet).** With the §0.6
  snippet, run
  `Invoke-RestMethod -Method Post "$api/orgs" -Headers $h -ContentType "application/json" -Body '{"name":"Side Project"}'`,
  then reload the app. **Expect:** the sidebar's org switcher offers both
  orgs, and each has its own, separate Assistants list. Switch back to QA
  Owner's Org for the rest of this guide.
- [ ] **2.6 Invite.** Go to **Members**, invite `qa-admin@example.com` as
  **admin**, and **Copy** the link. In the admin's browser, register that
  account, then open the link and join.
  **Expect:** the admin now has QA Owner's Org in their org switcher; select
  it. Repeat for
  `qa-member@example.com` as **member**. Members lists all three with the
  right badges.
- [ ] **2.7 Invite for someone else.** Open an invite link while signed in as
  the *wrong* account. **Expect:** a clear "this invite is for another
  address" view, not a silent join.
- [ ] **2.8 No self-promotion (API).** Log in as the admin in the §0.6 snippet
  (swap the email) and run:
  ```powershell
  $me = Invoke-RestMethod "$api/auth/me" -Headers $h
  Invoke-RestMethod -Method Patch "$api/orgs/$org/members/$($me.user.id)" -Headers $h -ContentType "application/json" -Body '{"role":"owner"}'
  ```
  **Expect:** 403 `role_change_not_allowed`. An admin also can't demote the
  owner (use the owner's user id): also 403.
- [ ] **2.9 Outsider isolation.** Later, once the owner has an assistant, open
  its build URL (`/assistants/<id>/build`) as the outsider.
  **Expect:** "not found". It must not say "forbidden", which would leak that
  the assistant exists.
- [ ] **2.10 Session refresh.** In `.env`, set `JWT_ACCESS_TTL_SECONDS=60` and
  restart the API. Open the build page in two tabs, wait two minutes, then
  click around in both. **Expect:** you stay signed in in both tabs. The
  Network tab shows `POST /auth/refresh` succeeding, with no sign-out and no
  401 loop. Set the value back to `900` afterwards.
- [ ] **2.11 Theme.** Click the theme button above your email in the sidebar.
  **Expect:** it cycles System → Light → Dark, and the canvas follows.

---

## 3. Building an assistant (Phase 1)

As the owner, in QA Owner's Org.

- [ ] **3.1 Guided setup.** On Assistants, click **Guided setup**. Name the
  assistant **Shop Helper**, and give it a system prompt such as "You help
  customers of an electronics shop." Go through the steps and create it.
  **Expect:** you land on the canvas with Input → Guardrails → Agent → Output,
  plus Memory.
- [ ] **3.2 Quick create.** Also create **Scratch Bot** from the quick-create
  box. It's used to test deletion later.
- [ ] **3.3 Node drawers.** Click the Agent node. **Expect:** a drawer with the
  system prompt, model, effort, max turns and budget. Change the system
  prompt, then open **Config JSON**. **Expect:** the new prompt is there.
- [ ] **3.4 Structural nodes can't be deleted.** Select the Agent node and
  press Delete. **Expect:** nothing happens. The same holds for Input,
  Output, Guardrails and Memory.
- [ ] **3.5 Tidy up.** Drag nodes around, then click **Tidy up**.
  **Expect:** a neat left-to-right layout. Clicking it again changes nothing.
- [ ] **3.6 Tabs by keyboard.** Click **Canvas**, then use ← → Home End.
  **Expect:** the tab selection moves, and Tab leaves the strip in one press.
- [ ] **3.7 Panels ↔ canvas.** In **Panels → Tools**, turn on **Calculator**.
  **Expect:** a tool node appears on the canvas, wired to the Agent. Turn it
  off again, and the node disappears.
- [ ] **3.7a Canvas stays visible.** Switch between Canvas and other tabs a
  few times, and delete a deletable node (select it, press Delete).
  **Expect:** the nodes are always drawn; nothing goes blank until a tab
  switch.
- [ ] **3.7b Wiring by hand.** Each node has an output dot on its right and
  an input dot on its left. Drag from an output dot towards another node's
  input: the dot turns **green** over an input that accepts it, **red** over
  one that doesn't, and releasing within about 40 px of a green dot connects.
  Alternatively, click an output dot, then click an input dot. To remove a
  wire, click it (it highlights) and press Delete.
- [ ] **3.8 Guardrail rules.** In **Panels → Guardrails**, add the rule
  `Never promise delivery dates.` **Expect:** Config JSON shows it under
  `guardrails.rules`. The fake driver doesn't read the system prompt, so its
  *effect* only shows with a real model (§9).
  > The switches for PII redaction, injection scan and refusal fallback, and
  > the memory options (summarization, auto titles, memory tool), are saved
  > but not enforced yet: enforcement is Phase 5. Only check that they save.
- [ ] **3.9 Publish with a note.** Click **Publish** and enter the note "First
  version". **Expect:** a "Published version 1" toast. Change the system
  prompt, then publish again with "Friendlier prompt".
- [ ] **3.10 History.** Open **History** and compare v1 with v2.
  **Expect:** the diff shows `system_prompt` changed, with both notes listed.
- [ ] **3.11 Delete an assistant.** On Assistants, click **Delete** on Scratch
  Bot. **Expect:** a dialog, not a browser pop-up. **Cancel** keeps it;
  confirming removes it and shows a "Deleted" toast.

---

## 4. Chat (Phase 1)

Turn **Calculator** back on for Shop Helper, then open **Chat**.

- [ ] **4.1 Echo.** Click **Start a conversation** and send `hello`.
  **Expect:** `(fake driver) you said: hello` streams in. A spend meter
  appears top right (e.g. `$0.0001 · 12 tokens`).
- [ ] **4.2 A tool call.** Send `calculate 21 + 21`. **Expect:** a
  **calculator** card marked **success**; expanding it shows input and output,
  and the answer is `42`.
- [ ] **4.3 Run details.** Click **Run details** under the answer.
  **Expect:** model, driver `fake`, tokens, cost, duration and the tool step.
- [ ] **4.4 Spend meter.** Hover the meter. **Expect:** tokens in / out. After
  each turn the total goes up.
- [ ] **4.5 Rename and archive.** Rename the conversation to "Maths"; the
  dialog is prefilled. Then **Archive** it: a confirm dialog appears, and the
  conversation leaves the list.
- [ ] **4.6 Stop.** Start a new conversation. Send any message and click
  **Stop** while it's streaming (the fake driver is fast; this is easier in
  §6.5 while an approval waits). **Expect:** the partial answer stays, and the
  run is recorded as aborted.
- [ ] **4.7 Budget cap.** On the Agent node, set **Budget (USD)** to `0.0002`,
  then send a few messages. **Expect:** the next message after the cap is
  refused with a "spend limit" error. Clear the budget afterwards.
- [ ] **4.8 Someone else's conversation.** As the **member**, open Shop
  Helper's chat and pick the owner's conversation. **Expect:** you can read
  it, but **Rename** and **Archive** fail with "Only the person who started
  this conversation, or an admin, can change it". As the **admin**, both
  work.
- [ ] **4.9 Editing someone else's assistant.** As the member, open Shop
  Helper's build page and change the system prompt. **Expect:** the save is
  refused ("You can only edit assistants you created…"), and the canvas
  reverts. As the admin, it saves.
- [ ] **4.9a Conversations follow the published version.** In an existing
  conversation, send `hello` and open **Run details**: note the **Version**
  (e.g. v2). Change the system prompt on the build page *without* publishing,
  and send again. **Expect:** still v2 (drafts never reach chat). Publish, and
  send again in the **same** conversation. **Expect:** Version v3.
- [ ] **4.9b Markdown.** With the knowledge base wired (§5), ask
  `search: refund eligibility`. **Expect:** the kb_search card's output shows
  headings and bold text formatted, not `##` and `**`.
- [ ] **4.9c Working indicator.** Send any message. **Expect:** while nothing
  has streamed yet, three animated dots in the reply bubble, and a pulsing
  **running** badge next to the spend meter.
- [ ] **4.9d Slash commands.** Type `/` in the message box. **Expect:** a
  menu of commands; ↑/↓ move, Tab completes, Enter runs, Esc closes. Try:
  - `/help` lists them all;
  - `/cost` shows the spend in a toast;
  - `/retry` resends your last message;
  - `/rename Refund questions` renames the conversation;
  - `/export` downloads the conversation as a `.md` file;
  - `/new` (or `/clear`) starts a new conversation;
  - `/archive` asks, then archives;
  - `/stop` while a turn runs stops it;
  - `/nope` says it's an unknown command, and nothing is sent;
  - `//etc` sends the message `/etc`.
- [ ] **4.10 Usage and audit (API).** In the owner's PowerShell:
  ```powershell
  Invoke-RestMethod "$api/orgs/$org/usage?group_by=model" -Headers $h | ConvertTo-Json -Depth 5
  Invoke-RestMethod "$api/orgs/$org/audit-log" -Headers $h | ConvertTo-Json -Depth 5
  ```
  **Expect:** usage rows with tokens and cost, and audit entries for invites,
  publishes, renames and archives. As the member, the audit log returns 403
  and usage works.

---

## 5. Knowledge base (Phase 2)

On Shop Helper's build page, open **Sources**.

- [ ] **5.1 Upload.** Under **File**, choose all eight files in
  `testdata/manual/docs/`. **Expect:** each row goes from pending to
  processing to **ready**, with a count (for example `1 doc · 1 chunk`) and a
  line such as `Last index: 1 embedded · no cost`. `service-agreement-large.txt`
  shows about 78 chunks, and `server-log-no-spaces.txt` about 30.
  > The two large files are slow on the free local CPU model (minutes each;
  > the log file most of all, since text without spaces is about twice as
  > many real tokens as it looks). Upload them last, one at a time, and keep
  > working meanwhile. The worker embeds one batch at a time, so memory stays
  > bounded.
- [ ] **5.2 Progress.** Delete `service-agreement-large.txt` (confirm in the
  dialog) and upload it again. **Expect:** while it runs, the row shows
  `Embedding 0/78`, then `Embedding 32/78`, and so on. The local CPU model can
  take a minute or more.
- [ ] **5.3 Reindex reuses.** Click **Reindex** on `refund-policy.md`.
  **Expect:** back to ready with `Last index: 0 embedded, 1 reused · no cost`.
  Nothing was re-embedded.
- [ ] **5.4 URL and pasted text.** Under **URL**, add `https://example.com`.
  **Expect:** ready, with the page's text. Under **Text**, paste
  `Gift cards can be redeemed online only.` with the name "Gift cards".
- [ ] **5.5 A failing URL.** Add `https://nonexistent.invalid`. **Expect:**
  an **error** badge with the real reason. Delete it.
- [ ] **5.6 Files in MinIO.** In the MinIO console (§0.6), browse
  `assistant-uploads/data-sources/…`. **Expect:** the uploaded files. Delete
  one source in the app, and its object disappears.
- [ ] **5.7 Wire the knowledge base.** On **Canvas**, open **Add node**, add
  **Knowledge base**, then add data sources for refund-policy, warranty-terms,
  shipping-faq and employee-handbook. **Expect:** they're wired automatically
  (source → knowledge base → agent). Config JSON shows `rag.enabled: true`,
  and `source_ids` lists exactly those four.
- [ ] **5.8 Validation warnings.** Delete the Knowledge base node.
  **Expect:** the data-source nodes remain with an "orphan" warning badge,
  and the header shows warnings. Undo it by adding the Knowledge base back and
  the sources again.
- [ ] **5.9 Search with citations.** In Chat, start a new conversation and
  send `search: how long do refunds take?`. **Expect:**
  - a **kb_search** card whose output shows `[1] Refund policy … (source_id=…, score=0.9…)`;
  - an answer containing `[1]`;
  - a **Sources** panel listing "Refund policy" with a heading breadcrumb;
  - **Open** loads the file (a short-lived link to MinIO).
- [ ] **5.10 Numbering continues.** In the same conversation, send
  `search twice: warranty accidental damage`. **Expect:** two kb_search
  cards. The warranty passage is numbered **[2]** (the next free number),
  not [1], which is still the refund policy. The same passage found twice
  keeps one number.
- [ ] **5.11 PDF page numbers.** Click the Knowledge base node, set **Max
  tokens per chunk** to `128`, and **Reindex** employee-handbook in Sources.
  Then ask `search: how many days of paid leave do employees get?`.
  **Expect:** the citation says **p. 2**. At the default of 800 it would say
  p. 1–4, because the whole PDF fits in one chunk.
- [ ] **5.12 Scoping by source.** Ask `search: store opening hours`.
  **Expect:** no results, because store-hours isn't wired to the knowledge
  base. Add its data-source node, ask again, and it's found.
- [ ] **5.13 Citations off.** On the **Output** node, switch off **Number the
  sources in answers**. Ask a search again. **Expect:** results as `- …` lines,
  with no [n] markers and no Sources panel. Switch it back on.
- [ ] **5.14 Retrieval subagent.** In **Panels → Subagents**, turn on
  **Retrieval**. Ask `search: warranty length`. **Expect:** an **Agent** card
  labelled **subagent**. Expanding it shows the subagent's notes ("Searching
  the knowledge base…") and a nested **kb_search** card. The answer itself
  does **not** contain those notes. The canvas shows **no** warning on the
  Subagent node while a knowledge base is wired. It warns only if the
  knowledge base is removed, because then the subagent has nothing to search.
  Turn it off again.
- [ ] **5.15 Retrieval eval.** From `apps/api`, run:
  ```bash
  ..\..\.venv\Scripts\python.exe -m pytest tests/test_evals_retrieval.py -m integration -s -q
  ```
  **Expect:** it passes. With `-s` it prints the scores: recall@1 ≈ 0.917 and
  MRR = 1.000.

---

## 6. Databases (Phase 3)

On Shop Helper's build page, open **Databases**.

### 6.1 Postgres, read-only

- [ ] **6.1.1 Add a connection.** Engine **Postgres**, Name `Shop PG`, Host
  `localhost`, Port `45432`, Database `testshop`, Username `app`, Password
  `app`. Save, then click **Test**. **Expect:** a success message.
- [ ] **6.1.2 The password is never returned.** In the browser Network tab,
  inspect `GET …/db-connections`. **Expect:** no password anywhere in the
  response.
- [ ] **6.1.3 Deny the sensitive table.** In the connection's permissions,
  put `employee_salaries` in the deny list. Open **Schema**.
  **Expect:** customers, orders, products and support_tickets, and **no**
  employee_salaries.
- [ ] **6.1.4 Least-privilege help.** Open the least-privilege section.
  **Expect:** a ready-to-run `CREATE ROLE … GRANT SELECT …` snippet for
  Postgres. Switch the engine and the snippet follows.

### 6.2 Wire it and query

- [ ] **6.2.1** On **Canvas**, **Add node → Shop PG**. Open its drawer.
  **Expect:** **Expose writes to this assistant** is off and disabled, because
  the connection is read-only.
- [ ] **6.2.2** In Chat, start a new conversation and send
  `sql: SELECT name, city FROM customers ORDER BY id`.
  **Expect:** a **sql_query** card showing the SQL that actually ran (with
  `LIMIT` appended) and a table of 8 customers.
- [ ] **6.2.3** Send
  `sql: SELECT c.name, o.total FROM orders o JOIN customers c ON c.id = o.customer_id ORDER BY o.total DESC`.
  **Expect:** a table led by Farah Khan (499.98).

### 6.3 The SQL guard

Each of these is refused with the message shown and never reaches the
database:

- [ ] `sql: SELECT * FROM employee_salaries` → "table employee_salaries is denied for this connection"
- [ ] `sql: SELECT 1; DROP TABLE orders` → "exactly one statement per query is allowed"
- [ ] `sql: SELECT pg_sleep(8)` → "function pg_sleep() is not allowed"
- [ ] `sql: UPDATE support_tickets SET status = 'resolved' WHERE id = 1` →
  **denied** without an approval card; the message names what to turn on
- [ ] `sql: DROP TABLE orders` → refused: this connection cannot run schema changes

### 6.4 Row limit, allow-list, timeout

- [ ] **6.4.1 Row limit.** Set the connection's row limit to `3` and repeat
  6.2.2. **Expect:** 3 rows, with `LIMIT 3` in the shown SQL.
- [ ] **6.4.2 Allow-list.** Put `customers, orders` in the allow list, then
  send `sql: SELECT * FROM products`. **Expect:** "not in this connection's
  allowed tables". Schema shows only those two. Clear the allow list.
- [ ] **6.4.3 Statement timeout.** Set the statement timeout to `2000` ms and
  send
  `sql: SELECT count(*) FROM orders a, orders b, orders c, orders d, orders e, orders f, orders g, orders h`.
  **Expect:** an error after about 2 seconds, not a hang. The database
  enforces the timeout. Restore it to 10000.

### 6.5 Writes need a human

- [ ] **6.5.1 Allow writes.** In the connection's permissions, turn on
  **write**. On the canvas node, turn on **Expose writes to this assistant**.
- [ ] **6.5.2 Deny.** Send
  `sql: UPDATE support_tickets SET status = 'resolved' WHERE id = 1`.
  **Expect:** an **approval card** showing that exact statement, with its
  risk. Click **Deny**. **Expect:** the card says it was declined. Then
  `sql: SELECT id, status FROM support_tickets WHERE id = 1` still shows
  **open**.
- [ ] **6.5.3 Approve.** Send the same UPDATE and click **Approve**.
  **Expect:** `1 row(s) affected`, and the SELECT now shows **resolved**.
- [ ] **6.5.4 No remembered consent.** Send the UPDATE again. **Expect:** it
  asks again.
- [ ] **6.5.5 Reload while waiting.** Send an UPDATE and, while the card is
  waiting, reload the page. **Expect:** the card is gone. A turn currently
  lives on its connection, so the reload ends it: the run is recorded as
  **aborted**, its approval is closed, and the statement never runs.
  (Keeping turns alive across reloads and conversation switches is planned
  work.)
- [ ] **6.5.6 Stop while waiting.** Send an UPDATE and click **Stop** while
  the card is waiting. **Expect:** the turn ends within a second, the card
  disappears, and the statement never runs. Send the UPDATE again.
  **Expect:** exactly **one** new card, with no stale card from the stopped
  turn.
- [ ] **6.5.7 DDL still refused.** Send `sql: DROP TABLE orders`.
  **Expect:** refused. Write access doesn't grant schema changes.
- [ ] **6.5.8 Reset the shop data.** Re-run `.\scripts\seed-testdata.ps1`.

### 6.6 The canvas knows about permissions

- [ ] Turn **write** off on the connection while **Expose writes** is still on.
  **Expect:** a validation error on the database node, and Publish refuses,
  naming the switch. Turn Expose writes off, and the error clears.

### 6.7 Other engines

- [ ] **6.7.1 MySQL.** Engine **MySQL**, Host `127.0.0.1`, Port `43306`,
  Database `appdb`, User `app`, Password `app`. **Test** and **Schema**
  succeed with the same five tables.
- [ ] **6.7.2 SQLite.** Engine **SQLite (file)**. File path: the full path to
  `testdata\manual\shop.sqlite`, e.g.
  `D:\My Projects\Agentic Chat Assistant\testdata\manual\shop.sqlite`.
  **Test** and **Schema** succeed. To query it, remove the Postgres node, add
  this one, and repeat 6.2.2. The fake driver queries the first wired
  database.
- [ ] **6.7.3 MongoDB.** Engine **MongoDB**, Connection string
  `mongodb://root:rootpw@localhost:47017/?authSource=admin`, Database
  `testshop`. **Test** succeeds. **Schema** lists orders, reviews and
  staff_notes, with inferred fields. Deny-list `staff_notes`, and it
  disappears from Schema. (The fake driver has no Mongo phrase, so querying
  Mongo from chat needs a real model, §9.)
- [ ] **6.7.4 The connection string is secret too.** In the Network tab, the
  Mongo connection shows `has_connection_uri: true` and never the string.

### 6.8 Defence in depth: the database role

- [ ] Add a Postgres connection `Shop PG (read-only role)` with Username and
  Password `testshop_ro`. Turn on **write** in the app anyway. Wire it as the
  only database, with Expose writes on, and approve an UPDATE.
  **Expect:** the database itself refuses (permission denied), even though
  the app allowed it. `sql: SELECT * FROM employee_salaries` is refused by
  Postgres, even with the deny list empty.

### 6.9 Key rotation (dry run)

- [ ] From `apps/api`:
  ```powershell
  $env:NEW_APP_KEK = (..\..\.venv\Scripts\python.exe -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())")
  ..\..\.venv\Scripts\python.exe -m scripts.rotate_kek --dry-run
  Remove-Item Env:NEW_APP_KEK
  ```
  **Expect:** every stored secret checked, none changed, and no key printed.
  Don't run it without `--dry-run` unless you also update `APP_KEK` in `.env`
  and restart.

---

## 7. Org isolation, end to end

- [ ] **7.1** As the outsider, open the owner's assistant, conversation and
  data-source URLs. **Expect:** "not found" every time.
- [ ] **7.2** As the outsider, create your own assistant and upload
  `store-hours.txt`. As the owner, search for store hours in Shop Helper.
  **Expect:** the outsider's copy never appears. Each org only ever sees its
  own sources.

---

## 8. Optional: tracing in Langfuse

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml --profile observability up -d
```

- [ ] In `.env`, set `LANGFUSE_HOST=http://localhost:3001`,
  `LANGFUSE_PUBLIC_KEY=pk-lf-local-dev` and
  `LANGFUSE_SECRET_KEY=sk-lf-local-dev`, then restart the API.
- [ ] Send a `calculate 21 + 21` turn, then open http://localhost:3001 and sign
  in with `admin@example.com` / `change-me-local-only`.
  **Expect:** a trace named "chat turn", in a session named after the
  conversation id. It holds a **claude** generation with tokens and cost, and
  a **tool:mcp__caps__calculator** span.
- [ ] Clear the three variables and restart the API when you're done.

---

## 9. Optional: a real model (costs money)

Everything above ran on the fake driver. To see a real model use the same
tools, set `AGENT_DRIVER=claude` in `.env` (and `RAG_OFFLINE=0` for Voyage
embeddings), restart the API and worker, and try natural questions.

Start the API with `.\scripts\dev-api.ps1`. It restarts the whole API
process when code changes, instead of using `uvicorn --reload`. The Agent SDK
runs its bundled CLI as a subprocess, and on Windows `uvicorn --reload` either
can't start one or stops answering after its first reload (see
`apps/api/app/devserver.py`). If the API is started some other way,
`/readyz` reports the problem as
`agent_cli: error: … cannot start the Claude CLI`.

- "Which open support tickets are high priority?" (uses Shop PG)
- "What does the warranty exclude?" (uses the knowledge base, with citations)
- "What do the partner notes say about returns?" **Expect:** it reports the
  14-day window and does **not** follow the "ignore all previous
  instructions" line in that document.
- With Mongo wired: "Which product has the worst reviews?"

A handful of turns costs cents, not dollars, but it isn't free. Set both
values back (`AGENT_DRIVER=fake`, `RAG_OFFLINE=1`) and restart when you're
done.

---

## 10. Tools and MCP servers (Phase 4, tasks 4.1–4.10)

Everything here runs on the fake driver, at no cost, except web search,
which only a real model can use (§9).

### 10.0 Setup

- [ ] **10.0.1 Start the MCP runner** in a fifth window. Local-command MCP
  servers run there, never inside the API:

  ```bash
  .\scripts\dev-mcp-runner.ps1
  ```

  **Expect:** "MCP runner on http://127.0.0.1:8100". `/readyz` now shows
  `"mcp_runner":"ok"`. Stop the runner later and it shows an error there,
  and the instance still reports itself ready: only local-command servers
  depend on it.
- [ ] **10.0.2 An assistant to test with.** Create one named **MCP Lab**
  with the Guided setup or Create. Leave everything at its defaults.

The local test MCP server is the repo's own echo server,
`apps/api/tests/fixtures/mcp_echo_server.py`. It has three tools:

| Tool | Does | Declares itself read-only? |
|---|---|---|
| `echo` | returns the `text` it was given | yes |
| `environment` | reports its working directory, `HOME`, whether any platform secret leaked into it, and the value of `DEMO_TOKEN` | no |
| `spin` | burns CPU for `seconds` | no |

### 10.1 HTTP requests (4.1)

- [ ] **10.1.1 Turn it on.** In **MCP Lab** › Panels › Tools, switch on
  **HTTP requests**. Set **Allowed domains** to
  `https://API.GitHub.com/repos, *.example.org` and click away.
  **Expect:** it saves as `api.github.com, example.org`. On the Canvas, an
  **HTTP requests** tool node is wired to the agent, and its drawer shows
  the same settings.
- [ ] **10.1.2 A read.** In chat: `http: GET https://api.github.com/zen`.
  **Expect:** a tool card `http_request` (success) with `HTTP 200 OK`,
  `text/plain`, and one line of GitHub zen. No approval card: GET only
  reads.
- [ ] **10.1.3 Outside the allowlist:** `http: GET https://example.com/`.
  **Expect:** error `blocked: example.com is not in this assistant's
  allowed domains`. Nothing was sent.
- [ ] **10.1.4 Inside the network.** Clear **Allowed domains** (any public
  site allowed), then try each of these:
  - `http: GET http://127.0.0.1:8000/healthz` → `blocked: 127.0.0.1 is not a public address`
  - `http: GET http://169.254.169.254/latest/meta-data/` → the same (the cloud metadata address)
  - `http: GET http://localhost:6379/` → `blocked: localhost resolves to a private or reserved address`
  - `http: GET file:///C:/Windows/win.ini` → `only http and https URLs are allowed`
- [ ] **10.1.5 A write asks.** `http: POST https://api.github.com/markdown
  {"text":"hello"}`. **Expect:** an approval card reading
  `POST https://api.github.com/markdown` and the body. Click **Deny**:
  "A human reviewer declined this action", and nothing was sent.
- [ ] **10.1.6 Writes can be forbidden.** Panels › HTTP requests › "Requests
  that change something": the choices are **Ask a person first** and
  **Never allow** (writes always ask unless forbidden). Choose **Never
  allow** and repeat 10.1.5. **Expect:** refused at once, and the message
  says to change the HTTP request tool's approval setting. Set it back to
  **Ask a person first**.
- [ ] **10.1.7 The Approvals panel.** Panels › **Approvals** lists database
  writes, schema changes, HTTP writes (each: ask or never) and MCP server
  tools (ask, run read-only tools without asking, or never). Set **HTTP
  requests that change something** to **Never allow**. **Expect:** the HTTP
  tool's settings show a warning that the assistant's policy is stricter.
  Set it back.

### 10.2 Tools on the canvas (4.2)

- [ ] **10.2.1** Canvas › **Add node** › **Tools**: the four built-in tools
  are listed, and HTTP requests is marked **added**. Click **Date / time**.
  **Expect:** a node wired to the agent, and its drawer says it has nothing
  to configure. Chat: `calculate 2 + 2` still works (Calculator is
  separate).
- [ ] **10.2.2 Web search limits** (settings only; running a search needs a
  real model). Panels › switch on **Web search**, set **Searches per turn**
  to `2` and **Allowed domains** to `docs.python.org`. **Expect:** both save,
  and the Canvas node's drawer shows them. With `RAG_OFFLINE=1` (your
  `.env`), both places also show a yellow note: this instance is in offline
  mode, so web search is off for every assistant. The settings are kept.

### 10.3 URL sources can't reach inside (4.1)

- [ ] **10.3.1** Sources › add a **URL** source `http://127.0.0.1:8000/healthz`.
  **Expect:** it fails with `can't fetch … is not a public address`, instead
  of indexing the API's own response. A normal public URL still indexes.

### 10.4 Registering MCP servers (4.3)

- [ ] **10.4.1 Refusals, each with its reason.** **MCP** tab › **Add server**,
  **HTTP**:
  - name `My_Tools` → "Use lowercase letters, digits and hyphens…"
  - name `caps` → "That name is reserved."
  - URL `https://mcp.example.com/mcp?api_key=abc123` → "the URL appears to carry a credential (api_key)…"
  - URL `http://mcp.example.com/mcp` → "url must use https…"
  - header line `Host: evil` → "the Host header is set by the connection itself"

  Switch to **Local command** and put `--token ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`
  in the arguments → "an argument looks like a credential… use environment
  variables".
- [ ] **10.4.2 A local-command server.** **Add server**, **Local command
  (stdio)**:
  - Name: `echo`
  - Command: `D:\My Projects\Agentic Chat Assistant\.venv\Scripts\python.exe`
  - Arguments: `D:\My Projects\Agentic Chat Assistant\apps\api\tests\fixtures\mcp_echo_server.py`
  - Environment: `DEMO_TOKEN=my-demo-value`

  **Expect:** the row shows `stdio`, **not checked**, "Environment:
  DEMO_TOKEN (values hidden)", and its limits. `my-demo-value` appears
  nowhere on the page.
- [ ] **10.4.3 A remote server:** name `tickets`, **HTTP**, URL
  `https://mcp.example.com/mcp`, header `Authorization: Bearer test-123`.
  **Expect:** "Headers: Authorization (values hidden)".
- [ ] **10.4.4 Editing.** On `tickets`, **Edit**, put `X-Api-Key: other` in
  **Replace headers**, **Save**. **Expect:** "Headers: X-Api-Key". Switch it
  **off**: an **off** badge appears.
- [ ] **10.4.5 Names are unique.** Add another server named `echo`.
  **Expect:** "This assistant already has an MCP server named 'echo'".
- [ ] **10.4.6 In the database** (§0.6), values are sealed:

  ```sql
  SELECT name, header_names, env_keys, headers_secret_ref IS NOT NULL AS has_headers FROM mcp_servers;
  ```

  **Expect:** names only. The values are in `secrets`, encrypted.

### 10.5 The sandbox (4.4)

- [ ] **10.5.1** The MCP tab says local-command servers run in the MCP
  runner and lists what protects them. On Windows it also says memory, CPU
  and process limits need the Linux runner container. That's expected: on
  this machine the sandbox is partial.
- [ ] **10.5.2 Limits.** On `echo`, **Limits**: set Memory to `512` and Idle
  timeout to `300`, **Save limits**. **Expect:** the row reads "512 MB memory
  … stops after 5 min idle", and the other limits are unchanged.
- [ ] **10.5.3 Optional, the full Linux sandbox** (needs Docker Desktop):

  ```bash
  docker build -f docker/mcp-runner.Dockerfile -t assistant-studio-mcp-runner:dev .
  ```

  Then follow the "Sandbox-test the MCP runner image" step in
  `.github/workflows/ci.yml`. **Expect:** the probe prints every protection
  (memory, cpu time, processes, no new privileges…), `leaked: []`, and
  "internet: unreachable".

### 10.6 Checking servers and their tools (4.5)

- [ ] **10.6.1** On `echo`, **Discover tools**. **Expect:** "Found 3 tools",
  status **ok**, and a list: `echo` marked **read-only**, the other two
  **not stated**, each with its description and inputs.
- [ ] **10.6.2** On `tickets`, **Check**. **Expect:** "Check failed: could not
  resolve mcp.example.com", the status turns **error**, and the row shows
  when it was checked.
- [ ] **10.6.3** Stop the runner window (Ctrl+C) and **Check** `echo`.
  **Expect:** "The MCP runner is not reachable at http://127.0.0.1:8100…". Start
  the runner again.

### 10.7 MCP tools in chat (4.6)

- [ ] **10.7.0 Choose its tools on the canvas (4.10).** Canvas › **Add
  node**: an **MCP servers** section lists `echo` and `tickets`. Click
  **echo**. **Expect:** an MCP server node wired to the agent, and `echo` is
  now marked **added** in the menu. Open the node's drawer:
  - "Tools this assistant may use (0 of 3)": nothing is allowed until
    ticked;
  - `echo` is marked **read-only**, and `environment` and `spin` **may
    change things**;
  - "This server's rule" reads "Use the assistant default (Ask a person
    first)".

  Tick `echo` and `environment`. **Expect:** "(2 of 3)", the node's
  subtitle shows 2 tools, and under each ticked tool a rule picker that
  reads **Asks a person first**.

  (`.\scripts\mcp-allow.ps1 --assistant "MCP Lab" --server echo --tools
  echo,environment` does the same from a terminal.)

New fake-driver phrase: `mcp: <server>.<tool> {json input}`.

- [ ] **10.7.1 It asks.** `mcp: echo.echo {"text": "hello"}`. **Expect:** an
  approval card "Approval needed — echo: echo", **low risk**. The default
  rule for MCP tools is "Ask a person first". **Approve**, and the card
  `echo: echo` shows `hello`.
- [ ] **10.7.2 Only allowed tools exist.** `mcp: echo.spin {"seconds": 1}`.
  **Expect:** no tool call at all (just the fake driver's echo): `spin` isn't
  in the allowlist.
- [ ] **10.7.3 The server's view.** `mcp: echo.environment {}`, **Approve**.
  **Expect:** `cwd` and `home` are a private `…\Temp\mcp-…` folder,
  `"leaked": []` (none of the platform's secrets), and `demo_token` is your
  `my-demo-value` from the sealed environment.
- [ ] **10.7.4 Nothing left running.** After a turn, open
  http://127.0.0.1:8100/healthz. **Expect:** `"sessions": 0`.
- [ ] **10.7.5 A server that's down** doesn't break the chat. Stop the
  runner and send `mcp: echo.echo {"text": "x"}`, then approve. **Expect:** the
  tool fails with "The MCP server 'echo' is unavailable: …", and the turn
  still ends normally. Start the runner again.

### 10.8 Per-tool rules and the audit (4.7)

- [ ] **10.8.1 Let a read-only tool run.** In the `echo` node's drawer, set
  `echo`'s rule to **Run without asking**. **Expect:** the line under it
  turns to **Runs without asking**. Then `mcp: echo.echo {"text": "free"}`. **Expect:** it runs with no approval
  card. Open the tool card: "Ran without asking."
- [ ] **10.8.2 `auto` doesn't unlock tools that change things.** The
  drawer won't let you try: `environment`'s rule picker has no **Run without
  asking** option. The server enforces the same thing, so force it from a
  terminal:

  ```bash
  .\scripts\mcp-allow.ps1 --assistant "MCP Lab" --server echo --rule environment=auto
  ```

  `mcp: echo.environment {}`. **Expect:** it **still asks**: `environment`
  isn't declared read-only, so `auto` can't skip the person. **Deny** it;
  the card says "Declined by a person."
- [ ] **10.8.3 Forbid one tool.** In the drawer, set `environment`'s rule to
  **Never allow**. **Expect:** the line reads **Never runs**. Then
  `mcp: echo.environment {}`. **Expect:** refused at once: "This assistant's
  rules do not allow echo: environment…", and the card says "Not allowed by
  this assistant's rules." `echo` still runs freely.
- [ ] **10.8.4 One switch turns them all off.** Panels › **Approvals** › MCP
  server tools › **Never allow**. **Expect:** the `echo` node's drawer warns
  that MCP tools are switched off for the whole assistant, and every line
  reads **Never runs**. `mcp: echo.echo {"text": "x"}` is refused, even though
  `echo`'s own rule is **Run without asking**. Set it back to **Ask a person
  first**.
- [ ] **10.8.5 The audit survives.** Reload the chat. **Expect:** each tool
  card still says how it was permitted. In the database:

  ```sql
  SELECT tool_name, server, status, permission, created_at FROM tool_calls ORDER BY created_at DESC LIMIT 10;
  ```

  **Expect:** `mcp__echo__…` rows with server `echo` and permission `auto`,
  `approved`, `declined` or `refused`.
- [ ] **10.8.6 See it all:**

  ```bash
  .\scripts\mcp-allow.ps1 --assistant "MCP Lab" --show
  ```

- [ ] **10.8.7 A deleted server.** Add another server in the MCP tab (any
  name, e.g. HTTP `https://mcp.example.org/mcp`), add its node on the
  canvas, then delete the server in the MCP tab. **Expect:** that node's
  drawer says the server is not registered any more. Reload the page.
  **Expect:** a validation error on that node (the server doesn't exist).
  Remove the node, and the error goes.

### 10.9 The catalog (4.8)

- [ ] **10.9.1 It's listed.** MCP tab. **Expect:** under the intro, **Or
  start from a well-known server** with six cards: Everything, Time, Fetch,
  Memory, Sequential thinking (each **local**) and GitHub (**http**).
- [ ] **10.9.2 A remote preset.** Click **GitHub (remote)**. **Expect:** the
  form opens with name `github`, **HTTP**, URL
  `https://api.githubcopilot.com/mcp/`, and a header line
  `Authorization: `. Below it are the docs link and what the header needs.
  Click **Add server** without filling the header in. **Expect:** "Fill in
  Authorization.", and no server is added.
- [ ] **10.9.3 A local preset.** Click **Time**. **Expect:** the form
  re-fills: `time`, **Local command**, command `uvx`, argument
  `mcp-server-time`, and a note that it downloads its package on first use.
  Click **Cancel**. Only add it if you have `uv` installed and are happy to
  download that package. The catalog is a starting point, not an
  endorsement.
- [ ] **10.9.4 Taken names.** Add any server named `memory` (for example,
  the echo command from 10.4.2 under that name). **Expect:** the Memory card
  shows **added** and can't be clicked. Delete that server afterwards.

### 10.10 Not built yet (so not testable)

- MCP tools scoped to one subagent: an MCP node wired through a subagent
  still gives its tools to the main agent (Phase 5);
- web search itself (needs a real model, §9);
- per-server internet allowlists for the Docker runner (see open item 10 in
  `docs/IMPLEMENTATION_PLAN.md`).

## Reporting a failure

For anything that doesn't match, note:

- the step number and what you did;
- what you expected and what you saw;
- the request id (the `x-request-id` response header in the Network tab),
  which finds the matching API log line;
- for chat, the **Run details** of that turn.
