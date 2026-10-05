# Internal rollout plan: the Studio for builders, Chat for everyone

Assistant Studio stays **internal only**: no public or customer-facing
chat. This plan makes it excellent inside a company, with two experiences
on one platform:

- **Studio** (unchanged): owners, admins and builders draw assistants,
  connect data, run evals, submit them for approval, watch costs.
- **Chat** (new): every other employee gets a simple front door: the
  approved assistants they are allowed to use, a focused chat, their own
  history, their requests. On a computer or a phone.

Nothing is removed. Everything here comes from two pieces of research
(October 2026): how the leading internal-AI products work (ChatGPT
Enterprise, Claude Enterprise, Microsoft Copilot Studio and Agent Store,
Glean, Moveworks, Dust, Langdock, StackAI) and a read-only audit of this
codebase. The team's answers to the open questions (5 October 2026) are in
section 7 and are folded into the decisions below.

## 1. Decisions

| # | Decision | Why |
| --- | --- | --- |
| D1 | **Two front doors on one platform**: Studio for builders, Chat for employees. | Every leader converged on this split: Copilot Studio vs the Agent Store, Glean's builder vs its Agent Library, StackAI's builder vs published SSO interfaces. |
| D2 | **A new chat-only role, "Employee"**, below Member. Members stay builders. Invited people default to Employee. | The market's three tiers are use only, view config, and edit. Today a plain member can create assistants, read every draft, prompt and database host, and spend in a personal org. |
| D3 | **Audiences**: an assistant is published to everyone, to groups, or to named people. Employees see only what they are in. | People, groups and everyone are the three share scopes in ChatGPT, Claude, Copilot and Glean. |
| D4 | **Every assistant needs an admin's approval before anyone can chat with it.** A builder submits a version; an admin or the owner approves or rejects it with a note. Only approved versions answer in Chat. Each new version goes through approval again. The builder can test their own draft in the Studio (a preview, visible only to them and admins). A curated catalog with an "Official" badge, categories, search and starters. | The team's decision (section 7), and Microsoft's model: makers publish, an admin reviews, then it reaches people. Today any creator's publish instantly changes what everyone gets. |
| D5 | **Conversations are private between employees**: each person sees only their own. **Admins and the owner can read any conversation**, and every such read is recorded in the audit log. | The team's decision. Today any member can read any conversation and see titles in Usage. |
| D6 | **Answers trimmed to who is asking**: knowledge sources carry groups, and retrieval and citations only use sources the asker may read. An assistant is only offered to people who can reach all of its restricted knowledge. | Glean and Copilot answer "with the permissions of the signed-in user", never the builder's. The visibility rule is Dust's. Today retrieval knows nothing about the asker. |
| D7 | **Tiered actions, decided by admins**: reads run directly; low-risk writes on the asker's own data are **confirmed by the asker**; every other write waits for **an admin or the owner**, never the asker. Every decision is audited. | The team's decision, on the market's tiered model (Moveworks, Glean, Claude). Today the employee who triggered a write can approve it, admins have no queue, and decisions are not audited. |
| D8 | **Tools know who is asking**: the asker's identity is passed to tools by the platform (bound SQL parameters, HTTP headers, MCP metadata), never chosen by the model. | "Run as the end user" (Glean), and Microsoft's warning against connections that act with the builder's rights. |
| D9 | **Sign in with Google, or a password.** Google sign-in can be limited to the company's own Google Workspace domain. Groups are managed in the app at first; syncing Google Groups is a later option. Members can be removed and deactivated. | The team's decision. Google's sign-in tokens carry the person and their Workspace domain, not their groups (those need Google's Directory API and admin consent), so in-app groups come first. |
| D10 | **Email through Gmail SMTP** (an app password): invites, approval requests to admins, decisions back to builders and askers. | The team's decision. Today invites are links an admin copies by hand, and nothing notifies anyone. |
| D11 | **Spend and adoption per person and per group**: usage carries the user, budgets per user and group, and an analytics view per assistant (active users, conversations, thumbs, unanswered questions, cost). | Claude Enterprise's per-user and per-group limits; Glean's and Copilot's agent analytics. Self-hosting without per-seat fees is our edge, so spend control is the product. |
| D12 | **Phone-friendly Chat**: sign-in, invites and chat work on phones (an installable web app). The desktop-only rule stays for the Studio. | Employees ask quick questions on the move; Moveworks, Copilot and Claude all serve mobile. The chat page is already responsive; only sign-in blocks it. |
| D13 | **The pilot is an IT helpdesk assistant**, shipped as a sample with its own handbook and demo data. It is also the story for future demos. | The strongest public evidence of any internal use case, understood by everyone, and it exercises every part of the platform (section 2). |

## 2. The pilot: an IT helpdesk

Why this one: password resets alone are often cited as 20 to 50% of IT
tickets (Gartner), and helpdesk agents report 59 to 88% ticket avoidance
(Moveworks customers). Every viewer of a demo has asked IT for help, and
one assistant shows every feature of the platform:

| What the employee does | What it shows |
| --- | --- |
| "My VPN keeps dropping on the train." | Answers from the IT handbook, with citations to the right page. |
| "What's happening with my laptop ticket?" | A read-only lookup **as the asker**: only their own tickets (D8). |
| "I need access to Figma for the redesign." | An access request: the employee checks the details on a card, an admin gets an email, approves in the inbox, the employee is told (D7, D10). |
| "Reset my password." | A low-risk change to their own account, confirmed by the employee alone. |
| "None of this worked." | Hand-off: the conversation goes to the IT team's contact. |
| (Builder) Submits version 2 with a new policy page. | The approval gate: version 2 only answers once an admin approves it (D4). |
| (Admin) Opens the assistant's analytics. | Active users, thumbs, unanswered questions to fill the handbook, cost. |

What we build for it (Phase 9): an **IT helpdesk sample** (like the store
support desk sample) with a short IT handbook (VPN, Wi-Fi, laptops,
passwords, software requests, security), its test questions, and a **demo
helpdesk database** (employees, tickets, software catalog, access
requests) for the test data and the videos.

Later use cases, by the same evidence ranking: HR policies and benefits,
team knowledge, support staff assist, data questions.

## 3. What each person sees

**Employee (Chat):**

- **Home**: the approved assistants they may use, Official ones first,
  with search, categories, an icon, a one-line description and the owner.
- **Chat**: up to six starters; streaming answers with citations that open
  the source only if they may read it; "I don't know" with a contact
  instead of guesses; Stop.
- **Their history**: search, rename, delete. Share a conversation as a
  link that only colleagues can open (an admin can turn sharing off).
- **Feedback**: thumbs up or down with an optional comment, and "Did this
  solve it?".
- **Action cards**: before a write, the collected details to check and
  edit, then the status (waiting for an admin, approved, done), and a **My
  requests** list.
- **Hidden**: the canvas, prompts, tools, raw SQL and rows, run details,
  costs, evals, other people's conversations. Errors say plainly what the
  assistant cannot do for them.

**Builder (Studio, unchanged, plus):** a draft preview to test while
editing, **Submit for approval** with a note on what changed, the status
of each submission, the audience, and each assistant's adoption, feedback
and unanswered questions.

**Admin and owner:** everything a builder has, plus two inboxes (**assistant
approvals** and **action approvals**), every conversation (each read
audited), the catalog's Official badges, groups, people (invite by email,
remove, deactivate), budgets per person and group, email settings, and the
audit log as a page.

## 4. Fix first: what the audit found

These are safe to fix before any new feature, and some are risks today:

1. **Every new account creates a personal organisation, as owner**, so
   each employee gets a studio of their own with spend outside the
   company's budget (`services/auth.py`). Invited people should join only
   the inviting organisation.
2. **The asker can approve their own write** (`services/approvals.py`).
3. **Any member can read any conversation**, and conversation titles and
   costs show on Usage for everyone (`services/chat.py`,
   `services/usage.py`).
4. **Any member can read every assistant's draft, prompt, data sources,
   database host and username, MCP settings and member emails**
   (`routes/assistants.py`, `db_connections.py`, `mcp_servers.py`,
   `orgs.py`).
5. **Nobody can be removed or deactivated**, and invites cannot be revoked
   (`routes/orgs.py`; `User.is_active` exists but nothing sets it).
6. **Archived assistants can still be chatted with**, never-published
   drafts are open to every member, and any creator can publish
   (`services/chat.py` `config_for`, `services/assistants.py`).
7. **Approval decisions, logins and conversation starts are not audited**
   (PRD FR-5 asks for approvals).
8. **Docs drift**: the PRD (FR-16) and the User Guide say chat runs the
   draft; the code runs the published version once one exists.

## 5. Phases

Each phase ends with the full check, like phases 0 to 6. Estimates assume
the same pace as before.

### Phase 7: Safe foundations (about 2 weeks)

The fixes in section 4, and the groundwork the rest builds on.

- 7.1 Invited people join only the inviting organisation; creating
  organisations becomes an owner-only setting.
- 7.2 Action approvals decided by admins and the owner only, never the
  asker; every decision audited.
- 7.3 Private conversations: each person lists and reads their own; admins
  and the owner read any, each read audited; titles and ids removed from
  Usage for non-admins.
- 7.4 Builder-only reads (drafts, prompts, connections, MCP, evals, member
  emails) behind the builder role.
- 7.5 Remove and deactivate members; revoke invites; sign a person out
  everywhere when deactivated.
- 7.6 The assistant approval gate: submit, approve or reject with a note,
  only approved versions answer; archived means closed; the builder's
  draft preview.
- 7.7 Audit coverage: logins, conversation starts, conversation reads by
  admins, approval decisions, memory cleared, file links opened; the audit
  log page.

### Phase 8: Chat for everyone (about 3 weeks)

- 8.1 The Employee role, and the landing page chosen by role.
- 8.2 **Sign in with Google** (limited to the company's Workspace domain if
  set), beside passwords; first Google sign-in through an invite links the
  account.
- 8.3 **Email through Gmail SMTP**: invites, assistant submissions to
  admins, decisions back to builders. Settings and a "send test email"
  button for admins; the app password lives only in the env file.
- 8.4 Audiences (everyone, groups, people) and groups managed in the app.
- 8.5 The catalog: Official badges, categories, search, starters.
- 8.6 The Chat app (its own route group and layout), reusing the chat
  components with the staff-only panels hidden.
- 8.7 My history across assistants, shared links inside the organisation,
  feedback (thumbs, comment, "Did this solve it?").
- 8.8 Phone sign-in and invites, an installable web app, phone-friendly
  sending.

### Phase 9: Permissions, actions and the pilot (about 3 weeks)

- 9.1 Knowledge sources with groups; retrieval, citation carry-over and
  file links filtered by the asker; the assistant visibility rule (D6).
- 9.2 The asker's identity passed to tools: bound SQL parameters (row
  filters by employee id), HTTP headers, MCP metadata.
- 9.3 Tiered actions: asker-confirmed cards for low-risk writes, admin
  approval for the rest, with timeouts.
- 9.4 The action approvals inbox for admins with email notifications, and
  My requests for askers (told by email when decided).
- 9.5 Hand-off to a person: the conversation sent to a named contact by
  email.
- 9.6 **The IT helpdesk sample**: handbook, test questions, the demo
  helpdesk database, and a test-drive section for it.

### Phase 10: Insight and lifecycle (about 2 weeks)

- 10.1 Usage carries the user; budgets per person and per group.
- 10.2 Analytics per assistant: active users, conversations, thumbs,
  unanswered questions (to fill knowledge gaps), cost.
- 10.3 Retention: delete conversations older than a set age; export or
  erase one person's data.
- 10.4 Later, by demand: syncing Google Groups (Directory API), Slack and
  Teams, document freshness reviews.

## 6. What stays exactly as it is

The Studio's canvas, panels, guided setup, knowledge bases, databases,
tools, MCP servers, evals, versions, run traces, budgets and the
self-hosted production stack. Builders and admins notice only additions
(and the approval step before an assistant goes live).

## 7. The team's answers (5 October 2026)

| Question | Answer | Where it lands |
| --- | --- | --- |
| Which sign-in? | Google and passwords. | D9, 8.2 |
| Who approves writes by default? | Admins (and the owner). Also: every assistant must be approved by an admin or the owner, and only approved assistants can be chatted with. | D4, D7, 7.2, 7.6 |
| Can admins read employees' conversations? | Yes. | D5, 7.3 (each read audited) |
| Email for notifications? | Gmail SMTP, with an app password the team already has. | D10, 8.3 |
| First pilot? | Left to the plan: the IT helpdesk (section 2). | D13, 9.6 |

Setup notes for the env file (never in the code or the chat):
`SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587` (STARTTLS), `SMTP_USERNAME`
(the Gmail address), `SMTP_PASSWORD` (the app password), `MAIL_FROM`; and
for Google sign-in, an OAuth client from Google Cloud Console
(`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, optional
`GOOGLE_ALLOWED_DOMAIN`) with the redirect URL
`https://<your host>/api/v1/auth/google/callback`. Gmail limits how much
mail one account sends a day (lower for a personal account than for
Workspace), which is plenty for invites and approvals.
