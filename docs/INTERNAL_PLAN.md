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

Two later studies (5 October 2026) reshaped the phases:
[`research/gap-review.md`](research/gap-review.md) (76 verified gaps plus
14 from a completeness critic, with evidence and fixes) and
[`research/opportunities.md`](research/opportunities.md) (what would make
the product better and unique). This plan carries their decisions and
order; the details of each item are in those two documents.

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
| D8 | **Tools know who is asking**: the asker's identity is passed to tools by the platform, never chosen by the model. For "only my own rows", the model cannot be trusted to write the filter: it uses **named queries** (SQL the builder writes, with the asker's id bound by the platform) or **row-level security** in the database. Identity is never sent to a host the model picked. | "Run as the end user" (Glean), and Microsoft's warning against connections that act with the builder's rights. The gap review showed bound parameters cannot constrain SQL the model writes. |
| D9 | **Sign in with Google, or a password.** Google sign-in can be limited to the company's own Google Workspace domain. Groups are managed in the app at first; syncing Google Groups is a later option. Members can be removed and deactivated. | The team's decision. Google's sign-in tokens carry the person and their Workspace domain, not their groups (those need Google's Directory API and admin consent), so in-app groups come first. |
| D10 | **Email through Gmail SMTP** (an app password): invites, approval requests to admins, decisions back to builders and askers. | The team's decision. Today invites are links an admin copies by hand, and nothing notifies anyone. |
| D11 | **Spend and adoption per person and per group**: usage carries the user, budgets per user and group, and an analytics view per assistant (active users, conversations, thumbs, unanswered questions, cost). | Claude Enterprise's per-user and per-group limits; Glean's and Copilot's agent analytics. Self-hosting without per-seat fees is our edge, so spend control is the product. |
| D12 | **Phone-friendly Chat**: sign-in, invites and chat work on phones (an installable web app). The desktop-only rule stays for the Studio. | Employees ask quick questions on the move; Moveworks, Copilot and Claude all serve mobile. The chat page is already responsive; only sign-in blocks it. |
| D13 | **The pilot is an IT helpdesk assistant**, shipped as a sample with its own handbook and demo data. It is also the story for future demos. | The strongest public evidence of any internal use case, understood by everyone, and it exercises every part of the platform (section 2). |
| D14 | **Ours first, others later**: the pilot runs inside our own company; everything stays installable by other companies (settings in env files, nothing company-specific in code, safe upgrades). A licence is chosen before anyone outside gets a copy. | The team's decision. There is no LICENSE file yet. |
| D15 | **Reached on the office network and over the company VPN** (or Tailscale), phones included. One company subdomain points to the server's private address; certificates come from a DNS check (a Caddy build with the DNS provider's plugin), so the server is never open to the internet. The server runs on a Linux machine, not a developer's Windows laptop. | The team's decision. Google sign-in and phones need a real HTTPS domain. Some office firewalls and home routers drop public names that point at private addresses (DNS rebinding protection), so the Phase 8 note covers split DNS as the fallback. |
| D16 | **An approved version is frozen**: its knowledge, connection permissions, tool definitions, model and catalog text are fixed at approval. Changing a shared source or connection that an approved assistant uses needs re-approval. Each conversation stays on the version it started on. Admins can **pause** an assistant or one tool, and **roll back** to the previous approved version. | The gap review: today an approval would not stop a builder changing live behaviour through a shared source or connection, and publishing is one step with nothing to review. |
| D17 | **Writes are declared actions that wait safely.** The builder declares each action and its tier; the platform does not guess from the SQL. An action needing an admin is stored with its exact input (encrypted), the conversation moves on, and a single executor runs it once after approval, re-checking every guard, then reports back into the conversation. Until that exists, today's in-chat approval stays as it is: admin-only approval must not ship with today's 5-minute in-memory wait, or every request would quietly expire. | Both studies found that approvals time out after 300 seconds and store only a redacted copy, so an admin who answers after lunch finds the request denied. |
| D18 | **Honest about what leaves the building**: answers use Anthropic's API; documents are embedded by Voyage unless the install uses the local embedding model already in the code. Opt out of Voyage training (its default terms reportedly allow it) or use local embeddings, and say so in the privacy notice. | The gap review (to confirm against Voyage's current terms). The self-hosted promise must be accurate. |
| D19 | **A security baseline before Chat opens**: authorisation denies by default, with a test of every role against every route; the server, not the interface, decides what an employee receives (no SQL, rows or costs in their responses); uploaded files can never run as part of the app; model output cannot leak data through images or links; the owner and admins use two-factor sign-in; every session can be ended. | The gap review's high findings (scripts in uploaded files, a fail-open role model) and the opportunity research's image-leak finding. |

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
| "I'm locked out of my laptop." | Steps from the handbook, then a request to IT. Resetting passwords through chat is deliberately not automated: the chat session would be the only proof of identity (gap review). |
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

## 4. Known risks today

The audit and the gap review found real issues in the current product.
They are fixed before anything new opens to employees (Phases 7a and 7b).
Full evidence and fixes: [`research/gap-review.md`](research/gap-review.md).

High:

1. **Uploaded files are served from the app's own address with the type the
   uploader chose**, so a builder could upload an HTML file whose script
   steals an admin's sign-in when the citation is opened.
2. **Model output can render images and links to any address**, the way
   known attacks leaked data (opportunity research; to confirm by test).
3. **Authorisation fails open**: routes check membership only, so adding a
   new role grants it everything unless every route is changed.
4. **The Claude CLI's transcripts and scratch files live on the API
   container's disk**: lost on every deploy and outside every deletion path.
5. **An admin can post into an employee's conversation** and act as them.
6. **Voyage's default terms reportedly allow training** on documents and
   queries unless an admin opts out (to confirm).
7. **The default MinIO image is reportedly no longer published**, and the
   model list lacks the 5.5 models while pinning ids that may retire (to
   confirm).

Also from the first audit: every new account creates its own organisation;
the asker can approve their own write; any member can read any
conversation, every draft and every connection's host and username;
nobody can be removed; archived assistants still answer; approval
decisions are not audited; and existing approval bugs (a row can say
"approved" when nothing ran; a crashed turn leaves an approval pending for
ever).

## 5. Phases

Phases 7 to 10 are now about **17 weeks** for two people, not 10: the gap
review moved real work forward and split Phase 7. Each phase ends with the
full check. Two cut lines give the company something real early:

- **Soft pilot (end of Phase 8, about week 9):** the IT helpdesk answers
  questions from the handbook with citations, for everyone, with private
  history, feedback and hand-off by email. No actions yet; knowledge is
  "everyone" only.
- **Full pilot (end of Phase 9, about week 14):** tickets looked up as the
  asker, access requests approved by admins hours later, group-filtered
  knowledge.

### Track 0: alongside Phase 7 (not engineering)

The pilot's content has the longest lead time, so it starts now:

- The company's real IT handbook, scrubbed of passwords and secrets.
- A named IT owner (answers hand-offs) and the admins who approve.
- Which ticketing system the company uses, if any (decides Phase 9).
- The server: a Linux machine on the office network, its backups, who
  holds the keys; the company subdomain and DNS provider.
- Voyage training opt-out, or a decision to use local embeddings.
- The Anthropic account's usage tier and spend limit (it can stop the
  whole pilot), and the 5.5 model check (one paid call, approved first).

### Phase 7a: Security and platform fixes (about 2 weeks)

Blocking issues in today's product. Nothing new for employees yet.

- 7a.1 Files are served safely: the server chooses the type, everything
  but PDF downloads instead of opening, no scripts on the files path.
- 7a.2 Model output cannot leak data: no external images, external links
  show their host, an image policy on both the app and Caddy.
- 7a.3 Authorisation denies by default, with a role-by-route matrix test
  (this makes the Employee role safe to add later).
- 7a.4 Admins read conversations but cannot post into someone else's.
- 7a.5 The CLI's transcripts and scratch files move to a volume, with a
  cleanup and a deletion path.
- 7a.6 Supported images and pinned versions: a maintained object store
  image, the CLI and other floating dependencies pinned, model ids behind
  aliases, the 5.5 models added.
- 7a.7 Existing approval bugs: "approved" only when the action ran;
  crashed turns release their pending approvals.
- 7a.8 Phones on weak networks stay signed in (a failed check no longer
  clears the session); logs kept long enough (CERT-In asks for 180 days).
- 7a.9 HTTP reads only go to allowed hosts.

### Phase 7b: Governance foundations (about 3 weeks)

- 7b.1 The approval gate (D4, D16): versions have a status (draft,
  submitted, approved, rejected); the reviewer sees what changed; approved
  versions are frozen; conversations stay on their version; pause and
  roll back; no way around it through other routes; the builder's draft
  preview is isolated.
- 7b.2 Private conversations (D5): each person sees their own; admins read
  any, with a reason, audited before the content is returned.
- 7b.3 Separation of duties: nobody approves their own write or their own
  assistant (the owner approves an admin's).
- 7b.4 People: invited people join only the inviting organisation; remove
  and deactivate; end every session; what a leaver leaves behind
  (ownership transfer, co-editors, pending approvals); the invite
  lifecycle.
- 7b.5 Email, moved forward from Phase 8: one mail service on a separate
  background queue with retries (Gmail SMTP first, any SMTP server later),
  links that never act on a single click.
- 7b.6 Password reset, change password and email verification (needs 7b.5).
- 7b.7 The audit log: complete coverage, tamper-evident, a page for admins.
- 7b.8 Migration and rollout: existing assistants and members carried
  over safely, organisation settings, changes behind switches.
- 7b.9 CI runs the Postgres and Redis test tiers.
- 7b.10 The threat model rewritten for internal use, before Chat opens.

### Phase 8: Chat for everyone (about 4 weeks)

Ends with the **soft pilot**.

- 8.1 The Employee role on the deny-by-default base; the server shapes
  what employees receive.
- 8.2 The internal hosting note and setup (D15): subdomain, DNS-challenge
  certificates, split DNS fallback, VPN, a preflight check.
- 8.3 Google sign-in done safely: the account keyed on Google's id, not the
  email; no automatic linking to a password account; domain, state, nonce
  and PKCE checks; first sign-in only through an invite.
- 8.4 Two-factor sign-in for the owner and admins; sign-in limits by
  person, not by office IP.
- 8.5 Audiences, groups in the app, the catalog (Official, categories,
  search, starters).
- 8.6 The Chat app: every state an employee can land in, deep links from
  emails, answers that survive a dropped connection, messages never lost,
  employee-friendly errors, the installable app.
- 8.7 A privacy notice and acceptable-use acknowledgement before the first
  chat; a card per assistant on what it uses and what leaves the building.
- 8.8 History: real delete, search across assistants, feedback (thumbs,
  comment, "Did this solve it?").
- 8.9 Per-person usage and spending caps (moved from Phase 10); alerts
  when Chat stops for everyone; the provider's spend cap handled.
- 8.10 One purge path and the retention rules, defined before employee
  conversations are collected.
- 8.11 Browser tests for the employee flows.

### Phase 9: Actions, permissions and the pilot (about 5 weeks)

Ends with the **full pilot**.

- 9.1 Declared actions with tiers, durable approvals with the exact input,
  one executor that runs once and reports back (D17); approver groups,
  delegation and reminders.
- 9.2 The approvals inbox, with safe email notifications; My requests.
- 9.3 Identity to tools done right (D8): named queries or row-level
  security; stored credentials for HTTP tools; reaching intranet systems
  through an allowlist.
- 9.4 Knowledge groups with revocation rules (D6), the shared knowledge
  library decision, a secret and Indian-ID scan at ingest; share links
  only now that knowledge is filtered.
- 9.5 Hand-off that creates a ticket in the chosen system (or an email to
  the IT owner), with a summary.
- 9.6 Screenshots in chat (the most common helpdesk input); secrets typed
  into chat detected and masked.
- 9.7 Builder evals and previews spend from their own budget, not the
  pilot's.
- 9.8 The IT helpdesk sample, the demo helpdesk database, and evals that
  test what matters (personas, several turns, "approval requested and
  nothing written").

### Phase 10: Insight and the signature loop (about 3 weeks)

- 10.1 Analytics per assistant with a baseline: resolution vs abandonment,
  cost per resolved question; reports for managers.
- 10.2 Traffic on the canvas: live calls, errors and approvals drawn on
  the line diagram (signature feature).
- 10.3 The needs-review queue: complaints and unanswered questions become
  test cases; the person who asked is told when it is fixed (signature
  feature).
- 10.4 Approve on evidence: the eval scorecard, the "no unapproved write"
  safety test and a cost forecast on the approval card (signature
  feature).
- 10.5 Retention and erasure in the admin interface (memory, traces and
  summaries included); conversation export.
- 10.6 Admin policies (models, web search, memory, sharing), India time
  for budgets and retention, Hindi and Hinglish basics.

Later, by demand (see [`research/opportunities.md`](research/opportunities.md)):
Google Drive knowledge that stays in sync, scheduled assistants, more
use-case packs (confidential HR, compliance, support-staff assist, finance
expense pre-check), local and India-hosted models.

## 6. What stays exactly as it is

The Studio's canvas, panels, guided setup, knowledge bases, databases,
tools, MCP servers, evals, versions, run traces, budgets and the
self-hosted production stack. Builders and admins notice only additions
(and the approval step before an assistant goes live).

## 7. The team's answers (5 October 2026)

| Question | Answer | Where it lands |
| --- | --- | --- |
| Which sign-in? | Google and passwords. | D9, 8.3 |
| Who approves writes by default? | Admins (and the owner). Also: every assistant must be approved by an admin or the owner, and only approved assistants can be chatted with. | D4, D7, D17, 7b.1, 9.1 |
| Can admins read employees' conversations? | Yes. | D5, 7b.2 (each read audited, with a reason) |
| Email for notifications? | Gmail SMTP, with an app password the team already has. | D10, 7b.5 |
| First pilot? | Left to the plan: the IT helpdesk (section 2). | D13, 9.8 |
| Who installs it? | Our own company first, other companies later. | D14 |
| How do employees reach it? | Office network and the company VPN, phones included. | D15, 8.2 |

Setup notes for the env file (never in the code or the chat):
`SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587` (STARTTLS), `SMTP_USERNAME`
(the Gmail address), `SMTP_PASSWORD` (the app password), `MAIL_FROM`; and
for Google sign-in, an OAuth client from Google Cloud Console
(`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, optional
`GOOGLE_ALLOWED_DOMAIN`) with the redirect URL
`https://<your host>/api/v1/auth/google/callback`. Gmail limits how much
mail one account sends a day (lower for a personal account than for
Workspace), which is plenty for invites and approvals.

## 8. Still to decide (Track 0)

1. Which Linux machine runs the pilot, who holds its keys, and where its
   backups go.
2. The company subdomain and its DNS provider (decides the Caddy plugin).
3. Voyage with training opted out, or local embeddings (slower, free, no
   documents leave for embedding).
4. Which ticketing system the company uses, if any.
5. Who the IT owner and the approving admins are.

