# Opportunity research: what would make Assistant Studio better, unique and most useful

October 2026. Internal-only direction. Produced by a research workflow: six research angles, a long list of opportunities, three judges (user value, uniqueness, feasibility for a two-person team), a synthesis, and a red team that challenged the top picks. Read-only: nothing in the app changed.

**How to read this:** items marked unverified were checked by reading code or docs, not by running anything. They must be confirmed before building. Competitor claims need re-checking against current docs before any marketing use (see the red team's corrections below).

## Positioning

Assistant Studio is a free, self-hosted platform for running internal assistants with governance built in. It is for IT teams at mid-size companies that have to show an assistant is safe, cost-capped and improving before every employee can use it, and keep showing it afterwards. The canvas is not the moat: Dify, Langflow, Flowise and n8n all have canvases, and n8n Chat Hub already offers a builder-plus-chat split. The moat is one release loop that no single competitor ships together in a free core. It runs: build on the line diagram, an eval scorecard plus safety tests (proving no write runs unapproved), admin approval on that evidence, pause and rollback, live traffic drawn on the same diagram, employee complaints turned into regression tests, and 'fixed in v3' sent back to the person who asked. The same governance covers actions: a request that writes data waits as durable, encrypted and audited until a named admin approves the exact statement, even hours later. Competitors put these features behind paid tiers (Dify Enterprise, Flowise Enterprise, Onyx EE, n8n Business, Open WebUI above 50 users), and Langflow and Flowise have had remote-code-execution flaws that were exploited in the wild, so a security-first free core stands out. The best early buyers are companies on Google Workspace or outside Microsoft, and Indian GCCs, IT/BFSI firms and manufacturers who want 'no per-seat fee, capped per-person spend in INR' against Copilot's per-seat pricing (the Rs 2,495 figure is secondary). Self-hosting, audited reads and DPDP-ready retention also make the product a compliance aid, but buyer demand for that is unverified, so test it with 2-3 IT heads before leading with it. Do not market unverified 'competitor X lacks Y' claims (Copilot eval-linked approvals, canaries, abstain gates) until they are checked against current docs.

> **Corrections from the red team (apply before using this anywhere):**
> Open WebUI is free at any size (its 50-user threshold covers branding only:
> https://docs.openwebui.com/license/), and it has audit logging, an
> admin-chat-access switch and a per-user permission preview. Copilot Studio
> now has evaluations, pre-publish checks and admin approval in its Agent
> Registry. Google Workspace customers already get Gemini included, so "no
> per-seat fee" does not sell to them. And the "free core" framing assumes a
> licence that has not been chosen (decided: ours first, others later; the
> licence is decided before anyone outside gets a copy).

## Signature features

1. Approve on evidence: when a builder submits a version, the admin's card shows the drawn version diff, eval pass rate and regressions, a 'no write ever runs unapproved' safety test result and a monthly cost forecast, plus pause and one-click rollback afterwards. This is the governed release loop competitors charge for or do not join up.
2. Traffic on the line map: the same transit-style canvas that defines the assistant lights up with live calls, error rates, latency, approvals and stale-source lamps per station, so builders and admins see behaviour where the design lives.
3. Complaint to 'fixed in v3': thumbs-down, 'didn't solve it', abstentions and hand-off outcomes land in one Needs-review queue, become regression tests with one click, are enforced by the gate, and the employee who asked is told when an approved version answers correctly.
4. Requests that wait, safely, with access you can see: writes become durable, encrypted, identity-bound requests that a named admin approves on the exact statement hours later, and every admin read of a conversation needs a reason and appears in the employee's own access log.

## Recommendations by tier

### Do next

#### Close the image/link data leak in the chat renderer before Chat opens to employees

- **Effort:** S (2-3 days). **Placement:** Phase 7 (new 7.8), before 8.6 Chat app.
- **Why:** Checked read-only (not exploit-tested): apps/web/components/chat/Markdown.tsx overrides only `a`, and the CSP in apps/web/next.config.ts has no img-src. EchoLeak (CVE-2025-32711) and Slack AI leaked data this exact way. Once 9.1 group-filtered knowledge exists, one poisoned document could send restricted chunks out of the company. It scores low on 'user value' but is a prerequisite: it must come before Phase 8.6 and before any chat attachments. Add an eval fixture so the CI gate proves it stays fixed, and record it in docs/THREAT_MODEL.md.
- **Red team (keep with changes):** Keep it as 7.8. Set img-src 'self' data: blob: in BOTH next.config.ts and the Caddyfile, and render model-produced images as a placeholder (or allow only same-origin citation and MinIO URLs). Show the full host of external links before opening them, or strip their query strings. Block web search, or warn at publish time, on any assistant whose knowledge sources are group-restricted once 9.1 lands. Add the eval fixture (a poisoned document that tries an image beacon and a search-query leak) and record both channels in docs/THREAT_MODEL.md. 2-3 days is realistic.
- **Judges' total:** 17 of 30. **Evidence:** Verified read-only: Markdown.tsx overrides only `a` (line 45), with no img renderer and no disallowedElements or urlTransform. The CSP in next.config.ts is only "frame-ancestors 'none'; object-src 'none'; base-uri 'self'". EchoLeak: https://arxiv.org/abs/2509.10540 ; Slack AI: https://simonwillison.net/2024/Aug/20/data-exfiltration-from-slack-ai/ . Not exploit-tested.

#### Add Opus 5.5 / Sonnet 5.5 with a per-model 'thinking off' mapping

- **Effort:** S (1-2 days plus one user-approved paid check). **Placement:** Phase 7 (maintenance).
- **Why:** apps/api/app/agent/models.py lists only haiku-4-5, sonnet-5 and opus-5. options.py (around line 419) sends {"type":"disabled"} whenever adaptive thinking is off, which the 5.5 models reportedly reject. Opus 5.5 at $4/$20 is cheaper than Opus 5, so the judge and budgets benefit. This is maintenance with no uniqueness, but it blocks the pilot. The pricing and thinking behaviour come from a bundled reference, so confirm against platform.claude.com before coding. Add a fake-driver test, and run one real call only after the user approves the spend (dev stays AGENT_DRIVER=fake).
- **Red team (keep with changes):** Build one per-model capability table: thinking-off mapping (Opus 5.5 means omit thinking and lower effort; Sonnet 5.5 means between_tools; Haiku 4.5 means no adaptive), an explicit effort on every request, and prices. Add a fake-driver test for each model. Add a refusal-to-friendly-message path on the SDK route. Add 5-10 IT/security helpdesk prompts to the eval suite to catch classifier false positives. Run one real call only after the user approves the spend. Keep it in Phase 7.
- **Judges' total:** 11 of 30. **Evidence:** Verified: models.py lists only claude-haiku-4-5, claude-sonnet-5 and claude-opus-5, with judge set to claude-opus-5. options.py line 419 sends {"type": "disabled"} when adaptive thinking is off. The 5.5 behaviour comes from the bundled claude-api reference (confirm at https://platform.claude.com/docs/en/about-claude/models/overview.md).

#### Durable admin approvals: a PendingAction table, encrypted exact input, executed by Arq after approval

- **Effort:** M (8-10 days). **Placement:** Phase 9 (replaces 9.3 'with timeouts'; feeds 9.4 inbox).
- **Why:** This is a fix to 9.3/9.4, not new scope. Both points below were checked in the repo: approval_registry.py DEFAULT_TIMEOUT_S=300 denies on timeout, and services/approvals.py stores redacted input that cannot be replayed. As planned, an admin who answers after lunch finds the request silently denied. Every 'chat that gets things done' use case (IT access, HR letters, reimbursements) depends on this. Build the schema-driven action card (typed, editable form from the tool's input schema) here as the implementation of the plan's section-3 action cards, not as a separate feature. Re-run sql_guard at execution, show 'as of' details, and purge the encrypted input once decided. Do not depend on the Agent SDK 'defer' hook (one tool per turn, no subagents, 30-day session sweep, unverified with our SDK version). At most, run a half-day spike on it after the table exists.
- **Red team (keep with changes):** Keep it. Write tools become 'propose' tools that return 'queued #id' to the model straight away instead of blocking. Use a state machine (pending, approved, executing, done or failed), take a row lock at execution, set max_tries=1 on the Arq job with a deterministic job id, and require manual retry for writes. The execution path re-runs every guard and the version/pause check. Pending actions are cancelled or re-validated on pause or rollback. Email links only deep-link to the inbox. Document how the encryption key is rotated, and note that backups contain ciphertext. Re-estimate as M+ (10-14 days with the cards), or ship the cards as a fast follow.
- **Judges' total:** 23 of 30. **Evidence:** apps/api/app/agent/approval_registry.py (DEFAULT_TIMEOUT_S = 300.0; the docstring says a timeout denies); apps/api/app/config.py approval_timeout_s=300; services/approvals.py create() stores redact(tool_input). Defer: https://code.claude.com/docs/en/hooks (it works for one tool call per turn only, not inside subagents, and session files are swept after 30 days).

#### Decide the org knowledge library before adding groups to sources

- **Effort:** M (decision 1 day; build about 1-2 weeks, overlapping 9.1). **Placement:** Decide during Phase 8; build as the first step of 9.1.
- **Why:** models/rag.py puts assistant_id on DataSource, Document and Chunk, so one HR handbook used by three assistants is embedded and permissioned three times. Adding D6 groups to per-assistant sources and then migrating later means two migrations, and freshness, Drive and confidential-HR all depend on this choice. retrieve() already accepts source_ids and caps_rag._scope already intersects them, so the build is contained. The decision takes a day and should be made now. The build is the first part of 9.1. Record the source version in each run trace, because edits to a shared source change answers without a new assistant version.
- **Red team (keep with changes):** Keep the one-day decision in Phase 8, and decide explicitly where chunking, embedder and contextual-retrieval settings live (on the library source, with the node keeping only retrieval and rerank settings). Write the graph-spec migration and the diff impact into the decision record. Budget about 2 weeks, not 1-2 weeks overlapping 9.1. Record the source version and checksum per run as proposed.
- **Judges' total:** 13 of 30. **Evidence:** apps/api/app/models/rag.py (assistant_id on all three tables), rag/retrieve.py retrieve(..., assistant_id, source_ids), agent/caps_rag.py _scope, agent/citations.py chunks_by_id.

#### Approve on evidence: eval scorecard, 'no write runs unapproved' safety tests and a cost forecast on the admin's approval card

- **Effort:** M (1-2 weeks). **Placement:** 7.6 ships the plain gate; scorecard, safety checks and forecast as new 9.7.
- **Why:** Scored high on all three lenses. It turns D4 from a judgement call into evidence. It reuses evals/gate.regressions, services/diff.py, queue.enqueue_eval and Unattended mode. The new parts are a database-stored baseline (instead of fixtures/regression_baseline.json) and two EvalExpected checks (expects_approval, no_auto_writes), which enforce the PRD's 'unapproved risky action executed: 0' on every build. Add an 'AI-drafted test questions' assist so builders without a test suite are not blocked. The gate stays soft, and admins can override with a note. Trade-off: judge tokens on every submission, so cap suite size. Batch API plumbing does not exist yet, so do not count on the half-price discount in v1.
- **Red team (keep with changes):** Keep it as 9.7. Market it as 'built in, free, with write-safety proofs', not as something competitors lack. Show the forecast as a range from eval token usage times an admin-entered expected volume, labelled as an estimate. Hold the AI-drafted test questions out of the regression baseline until a human has reviewed them.
- **Judges' total:** 21 of 30. **Evidence:** apps/api/app/evals/gate.py (regressions, Baseline from fixtures/regression_baseline.json), evals/runner.py, queue.py enqueue_eval, services/diff.py, schemas/evals.py EvalExpected (contains, not_contains, tools, cites, refuses), services/chat.py Unattended, ToolCall.permission. External: https://openai.com/index/morgan-stanley/ ; https://careersatdoordash.com/blog/large-language-modules-based-dasher-support-automation/ ; https://learn.microsoft.com/en-us/microsoft-copilot-studio/agents-experience/analytics-agent-evaluation-create

#### Pause switch per assistant and per tool, plus one-click rollback to the last approved version

- **Effort:** S (2-3 days). **Placement:** Phase 7.6 (alongside the approval gate).
- **Why:** This is the cheap part of staged-rollout-kill-switch. A one-time approval does not cover an incident such as 'the access tool granted the wrong group'. Pausing only the write tool while Q&A keeps running is immediate and audited. It reuses versioning and the tool allow-list in options.py. Pin each conversation to the version it started on. Staged per-audience canaries wait until later (they double the support surface).
- **Red team (keep with changes):** Ship it in 7.6. Pause takes effect in the PreToolUse hook at the next tool call (including detached turns). Pausing cancels or holds that tool's pending actions. Rollback re-pins open conversations to the rolled-back-to version, with a notice in the conversation. Describe it as parity with quarantine, not as a differentiator.

#### Traffic mode: live calls, error rate, p95 latency and approvals drawn on the canvas nodes

- **Effort:** S (about 5 days). **Placement:** Phase 10.2 (analytics drawn on the canvas, not only a separate dashboard).
- **Why:** Joint top-scored (22) and low risk. It is the payoff of 'the canvas is the config': tool_calls already store tool_name, status, latency_ms and permission. graph/trace_nodes.nodes_for_tool maps tool calls to nodes, and StudioNode already takes time, trace, issues and diff props. It makes problems obvious where builders work and is the best demo visual. Add an index on tool_calls(assistant, created_at) and keep it staff-only.
- **Red team (keep with changes):** Keep it in 10.2. Either denormalise assistant_id and version_number onto tool_calls in a migration, or aggregate from Run plus ToolCall in a nightly or 5-minute rollup table. Filter by version so it pairs with rollback. Show 'no data' honestly, not zero. About 5-7 days.
- **Judges' total:** 22 of 30. **Evidence:** apps/api/app/models/conversation.py ToolCall (tool_name, status, latency_ms, permission); graph/trace_nodes.py; observability/collect.py already groups by tool_name and status; StudioNode.tsx takes issues, trace, time and diff props.

#### One 'Needs review' queue: thumbs-down, 'didn't solve it' and 'no relevant results' turns become eval cases, then the asker is told 'fixed in v3'

- **Effort:** M (1-2 weeks). **Placement:** Phase 10.2 (replaces the plain 'unanswered questions' list; needs 8.7 feedback and the 8.3 mailer).
- **Why:** This merges feedback-to-eval-loop (score 22) with the hand-off outcome and the unanswered-question signal (caps_rag.py:109 returns a fixed string we can count), as all three judges asked: one inbox, not four. 'Add to test suite' goes through services/evals.add_cases, the regression gate then blocks a version that fails the case, and the asker is notified when an approved version passes. MIT NANDA names 'does not learn from feedback' as the main pilot failure; treat its 95% figure as directional, since the method is disputed. Privacy trade-off: copy only the question with PII redacted, audit it under D5, show who wrote each expected answer, and let confidential assistants opt out.
- **Red team (keep with changes):** Keep it in 10.2 as one queue. Make 'Add to test suite' admin-only, or have it copy a redacted question that the admin confirms. The asker notification is admin-triggered ('send update') with the new answer attached, not automatic. Keep a holdout set so the suite is not overfitted. Exclude confidential assistants.

### Do soon

#### Hand-off that always works: an always-visible 'Talk to a person' button, a Claude-written summary, routing by topic, and a one-click outcome

- **Effort:** S (3-5 days). **Placement:** Phase 9.5 (extended).
- **Why:** Small extension of 9.5 with high value. Uber Genie's 48.9% helpful rate shows about half of questions need a fallback. The contact's outcome (resolved, needs a doc fix, bug) feeds the needs-review queue and is the honest denominator for deflection. Email a link, not the transcript, for confidential assistants.
- **Red team (keep):** Ship it with 9.5: an always-visible button, a summary, a static topic-to-contact map on the assistant, a one-click outcome, and a link (not the transcript) for confidential assistants. Cite Uber Genie only as 'roughly half of answers needed a fallback in one internal tool'.
- **Judges' total:** 19 of 30. **Evidence:** MIT human preference: https://www.rcrwireless.com/20250825/ai/7-takeaways-gen-ai-divide-mit ; Uber Genie's 48.9% helpful rate shows half of answers need a fallback: https://www.uber.com/gb/en/blog/genie-ubers-gen-ai-on-call-copilot/ ; plan 9.5.

#### Strict-citation mode on the knowledge node: below a retrieval score threshold, answer 'I don't know, contact X' instead of guessing

- **Effort:** S (2-3 days). **Placement:** Phase 9.1 (with the knowledge filtering work).
- **Why:** This is the cheap, deterministic slice of live-groundedness. Today 'say so if nothing relevant' is only prompt text (agent/subagents.py:41). It matters for HR, finance and compliance, where confident wrong answers cause harm (Stanford RegLab measured 17-33% hallucination in legal RAG tools). Show the abstention rate next to the threshold so builders do not over-tighten it, and count abstentions in the needs-review queue. The sampled live judge comes later (Later tier).
- **Red team (keep with changes):** Implement it as: below threshold, the kb tool returns a 'must abstain' result and the platform appends a system instruction. In strict mode, buffer the answer (no streaming) or stream with an 'unverified until cited' state, then replace uncited answers with the contact fallback. Calibrate the threshold per assistant from its eval set (show an abstain-vs-correct curve). About 4-5 days, not 2-3.

#### Transparent admin reads: a required reason, plus an 'Access to your conversations' panel for employees

- **Effort:** S-M (about 1 week for reason + panel). **Placement:** Reason field in 7.3/7.7; employee panel in 8.6; hash chain in Phase 12.
- **Why:** This keeps D5 (admins can read) and makes it trustworthy. 57% of workers hide AI use (KPMG), and quiet monitoring pushes people back to personal ChatGPT. The reason field documents purpose under DPDP s.7(i). Open WebUI lets admins read chats with no audit, which is the contrast. Both parts reuse the 7.7 audit rows. Needs team, HR and legal sign-off on an 'investigation' reason that shows the entry to the employee only after a delay. The hash-chain audit moves to Phase 12, so audit_log changes are designed once together with the DPDP and security packs.
- **Red team (keep with changes):** Ship the required reason (7.3/7.7) and the employee access panel (8.6). Drop the delayed 'investigation' mode from v1 unless HR or legal ask for it. The panel copy must say that infrastructure operators are outside the product's audit. Remove the Open WebUI 'no audit' claim from all materials. The hash chain stays in Phase 12.
- **Judges' total:** 19 of 30. **Evidence:** Open WebUI: https://github.com/open-webui/open-webui/issues/31413 ; https://docs.openwebui.com/security/chat-data-privacy-and-encryption/ ; Pinchy: https://heypinchy.com/vs/onyx/ ; KPMG: https://kpmg.com/xx/en/media/press-releases/2025/04/trust-of-ai-remains-a-critical-challenge.html ; DPDP 7(i): https://www.barandbench.com/law-firms/view-point/navigating-legitimate-use-exemption-employee-data-digital-personal-data-protection-act-2023 . Code: apps/api/app/models/audit_log.py.

#### Per-assistant 'confidential' setting: only a named reviewer group can read conversations, with a pre-chat notice and shorter retention

- **Effort:** S-M (4-6 days). **Placement:** Phase 9 (alongside 9.1/9.2; required by the HR pack).
- **Why:** D5's 'any admin can read' would discourage exactly the sensitive HR questions (harassment, medical leave, salary) that make an HR assistant worth having. IBM AskHR (94% answered without escalation) depends on employees asking those questions. Confidential assistants are excluded from shadow replay and the needs-review queue. This narrows a team decision, so ship it as opt-in after explicit sign-off.
- **Red team (demote):** Move it from Phase 9 to ship together with the HR pack, after the IT pilot. Get team sign-off first. Build it as the reviewer group, a pre-chat notice and exclusion from replay and needs-review. Add per-assistant retention when 10.3 lands.

#### PII and Indian-ID scan at ingest: high-density files must be given a group (or 'everyone' confirmed) before indexing

- **Effort:** S (2-3 days). **Placement:** Phase 9.1.
- **Why:** guardrails/pii.py already detects Aadhaar, PAN and Indian mobile numbers, but its docstring says it skips the knowledge base. Oversharing delayed 40% of Copilot rollouts by 3+ months (Gartner via Computerworld). This is the cheap slice of view-as. Show the matches so false positives on PAN-like strings can be confirmed past.
- **Red team (keep):** Ship it in 9.1 as a warn-and-confirm step (density threshold, matches shown, 'confirm everyone' recorded in the audit), not a hard block. Store the result in DataSource.ingest_report.

#### Build 8.2 as generic OIDC with a Google preset first (Entra, Zoho and Keycloak next), and 8.3 as any-SMTP with the Gmail preset

- **Effort:** S-M (+2-4 days over Google-only). **Placement:** Phase 8.2 and 8.3 (design choice; ship Google first).
- **Why:** Not unique, but building it generic from the start costs a few days, while retrofitting later means a rewrite. Google-only sign-in rules out Microsoft-shop and Zoho companies, and Dify and n8n charge for SSO, so giving it away free is a positioning point. Link password and OIDC accounts carefully to prevent takeover through matching emails. Zoho OIDC support is unverified.
- **Red team (keep with changes):** Ship Google first on a generic OIDC layer, plus any-SMTP. Add a preflight check that the configured host is HTTPS on a public-suffix domain when Google OIDC is enabled. Document 'real domain with private DNS' as the supported pattern. Rate-limit and batch outgoing mail, with a provider-limit warning. Keep account linking strict (verified email plus invite match).
- **Judges' total:** 15 of 30. **Evidence:** LibreChat access control: https://www.librechat.ai/docs/features/access_control ; Dify Enterprise: https://dify.ai/dify-enterprise ; Microsoft share (secondary, low confidence): https://mitigata.com/blog/microsoft-365-vs-google-workspace/ ; plan D9, D10, 8.2, 8.3.

#### Turn off free-typed npx MCP packages by default; builders pick only from an admin-approved preset list

- **Effort:** S (2-3 days). **Placement:** Phase 8 (with 7.4 builder-only controls).
- **Why:** This is the cheap slice of approved-mcp-catalog. Unvetted MCP packages are a supply-chain risk, and Flowise's CustomMCP RCE (CVSS 10, exploited in the wild) shows the downside. It reuses mcp/presets.py. The full registry-backed catalog with hash pinning comes later.
- **Red team (keep):** Ship it in Phase 8 with 7.4. Default to presets only, with an owner setting to allow custom packages. Soften the exploitation claim to 'critical RCE' unless a source is re-checked.

#### Economy mode: above 80% of budget, step down to the fallback model and lower effort instead of stopping at 100%

- **Effort:** S (3-4 days). **Placement:** Phase 10.1 (with per-person and per-group budgets).
- **Why:** A hard stop looks like an outage to employees, and spend control without per-seat fees is the pitch (D11). It reuses router.py, services/budgets.py and Run.fallback_model. Builders can exclude compliance assistants from stepping down, and eval comparisons pick the fallback model.
- **Red team (keep with changes):** Ship it in 10.1 after per-person budgets. Show a visible 'economy mode' notice to the employee and the admin. Require that the eval suite passes on the step-down model before an assistant may opt in, and default compliance-tagged assistants to opting out. Record it as route='economy' rather than overloading fallback_model.
- **Judges' total:** 18 of 30. **Evidence:** apps/api/app/agent/router.py (effort only); services/budgets.py (warn at 80%, stop at 100%); Run.fallback_model and Run.route. Gartner: https://www.gartner.com/en/newsroom/press-releases/2024-10-21-gartner-identifies-four-emerging-challenges-to-delivering-value-from-ai-safely-and-at-scale ; Copilot billing: https://learn.microsoft.com/en-us/microsoft-copilot-studio/billing-licensing

#### Value report per assistant: deflection, cost per resolved question, week-4 return rate, before/after each version, and a 30/60/90-day pilot scorecard by email

- **Effort:** M (about 1-1.5 weeks). **Placement:** Phase 10.2.
- **Why:** Unclear value is the top reason pilots are abandoned (Gartner, S&P). Run already records version_number, cost and route. Lead with explicit 'Did this solve it?' data, and show hours saved only as an admin estimate with its formula, because published hours-saved figures vary wildly. Goal metric shown on the approval card.
- **Red team (keep with changes):** Fold it into 10.2 as one value panel per assistant: deflection from 'Did this solve it?', cost per resolved question, and a before/after comparison by version_number. Defer the emailed scorecard and the hours-saved estimate until a pilot sponsor asks.
- **Judges' total:** 18 of 30. **Evidence:** Run fields (version_number, route, stop_reason, cost_usd, duration_ms) in apps/api/app/models/conversation.py; usage.py lacks user_id (planned 10.1). McKinsey: https://www.mckinsey.com/capabilities/quantumblack/our-insights/the-state-of-ai-how-organizations-are-rewiring-to-capture-value ; Gartner: https://www.gartner.com/en/newsroom/press-releases/2024-07-29-gartner-predicts-30-percent-of-generative-ai-projects-will-be-abandoned-after-proof-of-concept-by-end-of-2025 ; Microsoft: https://www.microsoft.com/insidetrack/blog/transforming-it-support-across-microsoft-with-the-employee-self-service-agent/

#### Source owners, review-by dates, weekly re-fetch on checksum change, stale-citation badges and an amber lamp on the knowledge node

- **Effort:** M (about 1 week). **Placement:** Phase 10 (pulled forward from 10.4).
- **Why:** Stale sources are the most common trust complaint, and auditors ask which SOP version answered. The compliance and HR packs need it. Pulled forward from 10.4. The judges note the S label was optimistic (migration, cron, mail, chat badges, canvas lamps), so plan for M. The SSRF guard must run on every re-fetch.
- **Red team (keep):** Ship it in Phase 10 as: owner, review-by date, reminder email, stale badge on citations, an amber lamp on the node, and a weekly re-fetch for URL sources only, with the SSRF guard on every fetch. Wait for the shared-library decision first.
- **Judges' total:** 17 of 30. **Evidence:** models/rag.py DataSource has uri, checksum, indexed_at and ingest_report, but no owner or review fields; services/data_sources.py reindex(); rag/fetch.py fetches once. Guru: https://help.getguru.com/docs/verifying-and-unverifying-cards ; Copilot analytics: https://learn.microsoft.com/en-us/microsoft-copilot-studio/analytics-improve-agent-effectiveness

#### An Official general 'Company Assistant' (drafting, summarising, rewriting) with a PII warning before sending

- **Effort:** S (3-4 days). **Placement:** Phase 10 (after 10.1 per-person budgets; Official in the 8.5 catalog).
- **Why:** This brings shadow-AI usage back inside the governed platform. MIT reports personal AI use at 90%+ of companies, and Harmonic found 8.5% of prompts contain sensitive data. Narrow helpdesk bots do not stop this. Not unique, and it competes with Gemini bundled in Workspace, so pitch governance and audit. It needs per-person caps and a cheaper default model, so place it after 10.1.
- **Red team (demote):** Make the pre-send PII warning (pii.py) a general Chat feature in Phase 8 or 10. Ship 'Company Assistant' only as a sample config after 10.1, and do not present it as a pillar. Pitch it only to non-Workspace, non-M365 buyers.
- **Judges' total:** 16 of 30. **Evidence:** MIT: https://fortune.com/2025/08/19/shadow-ai-economy-mit-study-genai-divide-llm-chatbots ; Harmonic: https://www.csoonline.com/article/3819170/nearly-10-of-employee-gen-ai-prompts-include-sensitive-data.html ; IBM: https://www.kiteworks.com/cybersecurity-risk-management/ibm-2025-data-breach-report-ai-risks/ . Code: guardrails/pii.py; samples/*.json format.

#### Scheduled assistant runs: admin-approved, budgeted, traced digests and reminders by email (timezone-aware)

- **Effort:** M (1-2 weeks). **Placement:** New Phase 11 (Improvement loop and proactive assistants).
- **Why:** Assistants that reach out keep the programme visible after launch, and this moves KPI digests, stale-ticket reports and onboarding check-ins from weak to good fit. worker.py's only cron today is mcp_health_sweep. Add a schedules table, a once-a-minute tick with a deterministic _job_id, and Unattended mode so writes go to the approval queue. Restrict recipients to people who can access the assistant, and default to off-peak hours because runs share concurrency with live chat.
- **Red team (demote):** Move it to Later, after Phase 10 adoption data shows a concrete request (for example IT stale-ticket digests to admins only). Ship admin-recipient-only first.
- **Judges' total:** 16 of 30. **Evidence:** worker.py: the only cron is mcp_health_sweep (verified line 141); services/chat.py Unattended; evals/runner.py proves unattended runs in the worker. Copilot scheduled prompts: https://techcommunity.microsoft.com/blog/nonprofittechies/automate-your-workflows-with-scheduled-prompts-in-microsoft-365-copilot-chat/4386018 ; Arq: https://arq-docs.helpmanual.io/

#### Minimal approval policies: one approver group per action, one amount or row-count threshold, and a backup approver after 24h

- **Effort:** M (about 1 week for the minimal version). **Placement:** New Phase 11.
- **Why:** D7 has a single 'admin or owner' tier, so the IT admin becomes the bottleneck for HR and finance requests. It builds on durable actions and services/approvals.assert_can_decide. Avoid policy sprawl: ship the minimum, define thresholds only on typed inputs, and add approval chains only if the pilot asks. Show the tier as a badge on the tool's canvas station.
- **Red team (keep with changes):** Keep the minimal version (approver group per tool, one threshold on a typed input, a 24h backup approver), but sequence it with the HR and finance packs, after the pilot. Not in the first Phase 11 slot.
- **Judges' total:** 16 of 30. **Evidence:** Competitor survey: n8n's tool review is described in the prompt (https://docs.n8n.io/build/integrate-ai/ai-examples/human-in-the-loop-for-tools); Copilot multistage approvals (https://learn.microsoft.com/en-us/microsoft-copilot-studio/flows-advanced-approvals). Ramp's threshold and needs-review pattern: https://ramp.com/blog/ramp-policy-agent-ga-launch . Code: services/approvals.assert_can_decide already has approver rank rules.

#### Data dictionary for database connections: business definitions, verified example question/SQL pairs, execution-accuracy evals, and table + CSV output

- **Effort:** M (1-2 weeks). **Placement:** New Phase 11.
- **Why:** DatabaseNodeData holds only connection_id, nl2sql and expose_write, so the model gets no business meaning. Swiggy's Hermes went from 54% to 93% accuracy after adding metadata (self-reported). Combined with the parse-based SQL guard and 9.2 row filters, it makes self-serve data questions safe. Use redacted sample values and demo or replica connections for evals. Start with tables, not charts.
- **Red team (keep with changes):** Keep it in Phase 11 with a smaller scope: table and column descriptions plus 10-20 verified question/SQL pairs that the builder approves, and table plus CSV output. Make execution-accuracy evals optional, against a builder-supplied read-only replica.
- **Judges' total:** 16 of 30. **Evidence:** apps/api/app/graph/nodes.py DatabaseNodeData (verified); datasources/sql_guard.py. Swiggy: https://bytes.swiggy.com/hermes-a-text-to-sql-solution-at-swiggy-81573fb4fb6e ; Pinterest: https://medium.com/pinterest-engineering/how-we-built-text-to-sql-at-pinterest-30bad30dabff ; Spider 2.0: https://arxiv.org/html/2411.07763v2

### Later

#### Sampled live groundedness judge with a budget cap and trend line

- **Effort:** M (1-2 weeks). **Placement:** Phase 11 (after the needs-review queue and Batch plumbing).
- **Why:** DoorDash cut hallucinations by about 90% with a live guardrail and judge. evals/judge.py already scores groundedness and citation validity. It adds recurring spend, and the Batch API plumbing it would use for half price is new work. Report trends, not verdicts on single answers. Its low scores feed the needs-review queue.

#### Cluster unanswered questions weekly and draft the missing handbook section for the owner to approve

- **Effort:** M (1-2 weeks). **Placement:** Phase 11/12.
- **Why:** This is how deflection keeps rising without changing models. Moveworks Knowledge Studio charges for this as a SaaS feature. Owner approval is always required (never auto-ingest), and hand-off transcripts are redacted before drafting. It is worth doing only once the needs-review queue has real volume.
- **Judges' total:** 16 of 30. **Evidence:** Moveworks Knowledge Studio: https://help.moveworks.com/docs/knowledge-studio ; plan 10.2. Code: DataSourceType.text exists in models/rag.py; embedders in rag/embedders/.

#### Replay last week's real questions against a submitted version and compare answers with the judge

- **Effort:** M (1-2 weeks). **Placement:** Phase 12.
- **Why:** It is good evidence for the long tail, but judges agree it adds cost and privacy risk for a small gain over the eval scorecard plus pause/rollback. Admin-only, audited, capped, never run on confidential assistants.
- **Judges' total:** 17 of 30. **Evidence:** apps/api/app/evals/runner.py (each case runs as a hidden conversation through chat.run_message), evals/judge.py, services/chat.py Unattended, Conversation.eval_run_id. Gartner agentic forecast: https://www.forbes.com/sites/robertszczerba/2026/07/07/why-40-of-agentic-ai-projects-may-be-canceled-by-2027/

#### Approve v2 for a pilot group first and compare its live rates with v1 on the approval card

- **Effort:** S-M. **Placement:** Phase 12.
- **Why:** Useful once there are several busy assistants, but it doubles the support surface. Pause and rollback cover the incident case now.

#### 'View as' person or group: which assistants, sources and tools they get, and sample retrievals, saved as an audited report

- **Effort:** M (about 1 week). **Placement:** Phase 12 (with the security review pack).
- **Why:** It removes the security team's main reason to stall, but it is only meaningful once 9.1/9.2 filters exist and groups are in real use. Read-only simulation: it must never let an admin chat as someone else.

#### DPDP/CERT-In pack: retention floors, rights-request tracker, breach export and a governance report per assistant

- **Effort:** M (1-2 weeks). **Placement:** New Phase 12 (Compliance and India), target Q1 2027.
- **Why:** DPDP core duties apply from 13 May 2027, and CERT-In requires 180 days of logs. models/audit_log.py has no retention logic. It must not be sold as legal advice, the exact retention periods need checking against the Rules text, and buyer pull is unverified. Design the audit schema once, together with the hash chain and the security pack. Target shipping before May 2027.
- **Judges' total:** 14 of 30. **Evidence:** DPDP Rules: https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf ; Rule 6 summary: https://www.dpdpa.com/dpdparules/rule6.html (log period from secondary summaries); CERT-In: https://trilegal.com/wp-content/uploads/2022/07/How-to-comply-with-CERT-Ins-new-six-hour-time-frame-to-report-cyber-incidents.pdf ; RBI FREE-AI: https://law.asia/rbi-ai-framework/ . Code: models/audit_log.py (no retention logic found).

#### Security review report generated from the live install for the CISO (data locations, outbound hosts, guardrail findings, backups, preflight)

- **Effort:** M (1-2 weeks). **Placement:** Phase 12.
- **Why:** It shortens security reviews. Langflow and Flowise CVEs on CISA's exploited list have made security posture a buying criterion. Every claim must be computed from the running system, not hard-coded. Owner-only and audited.
- **Judges' total:** 14 of 30. **Evidence:** Gartner via Computerworld (above); Langflow KEV: https://thehackernews.com/2025/05/critical-langflow-flaw-added-to-cisa.html . Code: docs/THREAT_MODEL.md, preflight and backup scripts, guardrails/turn.py findings (carry tool and detail), docker-compose.mcp-egress.yml, security/ssrf.

#### Model provider option: Amazon Bedrock (India) and a checked 'data stays in India' profile

- **Effort:** M (1-2 weeks). **Placement:** Phase 12.
- **Why:** Today every prompt and retrieved chunk goes to Anthropic's API abroad, which undercuts the self-hosting pitch for BFSI and GCCs. Bedrock India availability is reported (AWS blog, 29 Sep 2026), but pricing, model parity and the CLI environment variable names are unverified, and testing needs a real AWS account and spend. Verify first and ask before any real call.
- **Judges' total:** 13 of 30. **Evidence:** AWS India in-country Claude: https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-expands-claude-model-availability-to-india-cross-region-inference/ ; SEBI cloud: https://taxguru.in/sebi/framework-adoption-cloud-services-sebi-regulated-entities.html . Code: agent/options.py builds the CLI env; config.py has only anthropic_api_key; rag/embedders/local_bge.py and rerankers/local.py exist.

#### Indian language support: answer-language setting, Hinglish eval pack, Devanagari fonts, and a local IndicTrans2 MCP preset

- **Effort:** M (1-2 weeks). **Placement:** Phase 12 (eval pack and fonts can go earlier).
- **Why:** Employees type Hinglish, and models score worse on romanised text. Start with the cheap, measurable slice: the Hinglish eval pack and fonts (layout.tsx loads Fira Sans with the Latin subset only). Translation services such as Sarvam send data outside the box, so default to local. A quality eval set needs native speakers.
- **Judges' total:** 14 of 30. **Evidence:** https://arxiv.org/html/2512.10780v1 ; https://kiac.iisc.ac.in/wp-content/uploads/2025/06/HinglishEval.pdf ; IndicTrans2: https://github.com/ai4bharat/IndicTrans2 ; Sarvam API: https://docs.sarvam.ai/api-reference/chat/chat-completions . Code: apps/web/app/layout.tsx (Latin subset), rag/embedders/local_bge.py, mcp/presets.py.

#### Playbooks: approved, versioned, instruction-only Agent Skills loaded when needed

- **Effort:** M (1-2 weeks). **Placement:** Phase 12+.
- **Why:** Procedures fit poorly in RAG chunks, and each skill costs about 100 tokens until it is used. It requires changing setting_sources=[] (options.py around line 463), which risks loading host CLAUDE.md or settings, so it needs an isolated working directory and isolation tests first.
- **Judges' total:** 15 of 30. **Evidence:** https://code.claude.com/docs/en/agent-sdk/skills (filesystem-only, setting_sources, 'Skill' must be in the tools list); https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview.md . Repo: agent/options.py (setting_sources=[], tools=builtin_tools(spec), cwd=spec.cwd). LibreChat: https://www.librechat.ai/docs/features/agents

#### Files in a conversation (PDF/DOCX/XLSX/CSV), private to the person, retained under 10.3 rules

- **Effort:** M (1-2 weeks). **Placement:** New Phase 13 (Files and connectors, by demand).
- **Why:** It opens legal, sales RFP and finance AP use cases, but rag/parsers has no xlsx or csv parser and attachments are a new prompt-injection path. Exfil hardening must ship first. Build it only once a pilot department asks.
- **Judges' total:** 13 of 30. **Evidence:** rag/parsers/ has only pdf, docx, html, text (verified); no attachment upload in apps/api/app/api/routes/conversations.py or schemas/conversation.py (per research). Harvey/A&O: https://www.harvey.ai/customers/a-and-o-shearman ; Ironclad roundup (secondary): https://ironcladapp.com/journal/legal-ai/ai-for-legal

#### Export answers as DOCX/XLSX from company templates behind audited links

- **Effort:** M (1-2 weeks). **Placement:** Phase 13.
- **Why:** The legal, RFP and AP use cases end in a document. It depends on attachments and scheduled runs to be useful. Mark outputs 'AI draft' with citations.
- **Judges' total:** 11 of 30. **Evidence:** Agent Skills require code execution and are not covered by ZDR: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview.md ; RFP time-saving claims (vendor): https://autorfp.ai/blog/security-questionnaire-automation

#### analyze_file: pandas and matplotlib in the existing network-free MCP jail

- **Effort:** L (3-6 weeks). **Placement:** Phase 13.
- **Why:** It is real shadow-AI demand and keeps data on the box. But it is L effort for parity, and running model-written code is exactly where Langflow and Flowise got burned. It needs per-session isolation and a pen test. Only if buyers require it.
- **Judges' total:** 11 of 30. **Evidence:** apps/api/app/mcp/jail.py and runner.py (no network, umask 077, no_new_privs, rlimits); agent/caps_memory.py notes the CLI cannot be handed API tool definitions; agent/options.py DISALLOWED_TOOLS includes Bash. Code execution retention and ZDR: https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool.md

#### Google Drive connector with scheduled re-sync and Drive sharing mapped to groups

- **Effort:** L (3-6 weeks). **Placement:** Phase 13.
- **Why:** High-adoption assistants connect live content, and Onyx keeps permission sync for Enterprise only. But it is L effort, depends on the shared library, and mapping Drive groups needs the Directory API and admin consent. Start with user and domain shares, and only for a pilot buyer who asks.
- **Judges' total:** 12 of 30. **Evidence:** models/rag.py DataSourceType = file, url, text; worker.py only cron is mcp_health_sweep; Onyx permission sync EE: https://docs.onyx.app/admins/connectors/official/confluence ; Klarna: https://www.klarna.com/international/press/90-of-klarna-staff-are-using-ai-daily-game-changer-for-productivity/

#### Wire the unused api_tokens: REST and MCP access to approved assistants from inside the network

- **Effort:** M (1-2 weeks). **Placement:** Phase 13.
- **Why:** The api_tokens table exists but nothing uses it (checked). Not unique (Dify publishes apps as MCP servers) and it widens the attack surface. Token calls must run unattended, so writes go to the admin queue.
- **Judges' total:** 11 of 30. **Evidence:** apps/api/app/models/api_token.py; grep finds ApiToken referenced only by the model and models/__init__.py (verified); Conversation.external_user_ref; agent/caps_memory.py 'ext:' scope; PRD FR-4. Dify: https://dify.ai/blog/v1-6-0-built-in-two-way-mcp-support

#### Full MCP catalog fed by the official MCP Registry, with version and hash pinning and re-approval on upstream changes

- **Effort:** M (1-2 weeks). **Placement:** Phase 13.
- **Why:** It extends the allowlist slice. Hash pinning needs a fetch-and-verify step, and air-gapped installs need manual import.

### Skip

#### Google Chat / Teams channel (for now)

- **Effort:** L (3-6 weeks). **Placement:** Not planned; revisit after Phase 10 adoption data.
- **Why:** The adoption evidence is strong (Klarna Kiki, LinkedIn 5-10x), but it needs inbound access from Google to the box, which conflicts with the decided internal-only direction, and it is L effort. Revisit only if the team explicitly opts into documented ingress. Phone-friendly Chat (D12) covers 'where people are' for now.
- **Judges' total:** 12 of 30. **Evidence:** Google Chat interaction events: https://developers.google.com/workspace/chat/receive-respond-interactions ; Klarna: https://www.pymnts.com/news/artificial-intelligence/2024/klarna-reports-internal-ai-assistant-answers-2000-employee-questions-daily/ ; Uber Genie: https://www.uber.com/gb/en/blog/genie-ubers-gen-ai-on-call-copilot/ ; plan 10.4.

#### Anthropic-hosted code execution and pre-built document Skills

- **Effort:** n/a. **Placement:** Not planned.
- **Why:** They keep files for up to 30 days and are not covered by zero data retention, which breaks the self-hosted promise, and the Claude Code CLI cannot be handed API server tools anyway. Use the on-box jail instead if file analysis is ever built.

#### Building durable approvals on the Agent SDK 'defer' hook

- **Effort:** n/a. **Placement:** Not planned as a dependency.
- **Why:** Limits: one tool call per turn, no subagents, session files swept after 30 days, and it is unverified with our SDK version. The PendingAction table works regardless. At most, a half-day optional spike later.

#### Computer use, A2A, deep-research jobs, voice and speech-to-text

- **Effort:** n/a. **Placement:** Not planned.
- **Why:** Mature or interesting, but none serves the internal pilot. Deep research costs about 15x the tokens of chat, and voice adds a vendor data path. Not worth a two-person team's time now.

#### WhatsApp bot for frontline staff

- **Effort:** n/a. **Placement:** Not planned.
- **Why:** Limited by Meta's January 2026 ban on general-purpose AI bots (whether it applies to internal bots is unverified), costs money per message, and needs public ingress. The phone web app with QR entry covers frontline access.

#### Plant/field SOP pack as a near-term sample

- **Effort:** M (1-2 weeks). **Placement:** Deferred to Phase 12+.
- **Why:** It scored lowest (10). Its headline evidence (ACG Capsules 30-40%) is secondary and unverified. It needs sign-in for workers without accounts, which complicates D5 and D6, and it depends on the Indic pack. Keep it as a Phase 12+ idea for manufacturing buyers.

## Use-case packs (sample assistants with demo data)

### IT Helpdesk (the D13 pilot, upgraded)

- **Department:** IT
- **Flows:** Handbook Q&A with citations (VPN, MFA, laptop and printer issues); read-only lookup of the asker's own tickets and assets in the demo helpdesk database, through 9.2 row filters; access and software requests as schema-generated action cards that wait durably for an IT admin; 'Talk to a person' hand-off routed by topic; eval suite including expects_approval and no_auto_writes checks. Requires the sample rule change in 9.6 (tests/test_samples.py currently asserts databases == [] and mcp_servers == []). Do it once, for all packs.
- **Demo story:** An employee on her phone asks 'VPN keeps dropping on hotspot' and gets a cited fix. She then asks for Figma access. A form is pre-filled, she corrects the team field, and sees 'Submitted, waiting for IT admins'. Two hours later the admin approves the exact request from the email, an Arq job runs it, and she gets 'Done' in the conversation and by email. The builder switches the canvas to traffic mode: the access-tool station shows 12 calls today, 1 failure and amber latency. A thumbs-down on an MFA question goes to Needs review, becomes a test case, v3 passes the gate, and the employee gets 'Your question now has an answer (v3)'.

### HR Policies (confidential)

- **Department:** HR
- **Flows:** Handbook Q&A filtered by region and grade (leave, benefits, travel, POSH process, parental leave); demo HRMS leave balances and payslip months read as the asker; letter requests (salary or employment certificate) approved by the HR-leads group, not IT; confidential setting so only HR leads can read conversations, with a pre-chat notice, shorter retention and exclusion from the needs-review copy; strict-citation abstain mode.
- **Demo story:** An employee in Hyderabad asks about maternity leave and sees only the India policy, cited with its effective date. A London colleague asking the same question gets the UK policy. She asks for an employment letter for a visa, and HR leads approve it from email. She opens 'Access to your conversations' and sees that no one has read her chats. An IT admin tries to open an HR conversation, is blocked, and the attempt is audited. The admin's approval card for this assistant showed 38/40 eval cases passing and a forecast of about Rs 4,000 a month.

### Compliance and Policy Guidance

- **Department:** Compliance / Risk / Legal (BFSI first)
- **Flows:** Synthetic policy library with owners, review-by dates and versions; strict-citation mode (refuse without a passing source); citations showing clause, version and date; 'I don't know, contact Compliance' fallback with hand-off; adversarial eval set (outdated versions, conflicting clauses, out-of-scope questions); stale-citation badges; later, the per-assistant governance report.
- **Demo story:** An analyst asks 'Can I accept a Rs 8,000 Diwali gift from a vendor?' and gets the answer with clause 4.2 of Gifts Policy v3 (effective 1 Apr 2026). She asks about a policy that does not exist and the assistant declines with a Compliance contact instead of guessing. The admin opens the approval card, which shows the tricky-question eval set and the abstention rate. On the canvas the knowledge node shows an amber lamp because one source is past its review date, and its owner already has a reminder email. Use the Deutsche Bank/TCS 'over a day to seconds' figure as context, not as our result.

### Support Agent Copilot (internal staff)

- **Department:** Customer Support (used by the company's own agents, so it stays inside the internal-only rule)
- **Flows:** Reframe samples/store-support.json: the agent pastes a customer message; the assistant answers from the support handbook with citations, looks up order and account state in a read-only demo database, and produces an editable 'draft reply' block; refunds and credits above a threshold go to a team-lead approval; eval suite for grounding and tone; PII redaction in traces.
- **Demo story:** A new support agent pastes an angry message about a late refund. The copilot finds the order, cites the 7-day refund policy and drafts a reply, which the agent edits and copies. It proposes a Rs 500 goodwill credit, which waits for the team lead, who approves it in the inbox. The NBER field study (14% more issues resolved per hour, 34% for novices) is the framing. The builder's needs-review queue shows three drafts agents rejected this week, each one click away from becoming a test case.

### Finance Expense Pre-check

- **Department:** Finance / Accounts Payable
- **Flows:** Travel and expense policy Q&A with citations; 'where is my reimbursement?' read from a demo reimbursements table as the asker; pre-check that labels a claim Approve, Reject or Needs review with cited clauses, shown on the finance approver's card with an amount threshold. Track how often approvers override the label. Receipt upload waits for chat attachments (Phase 13), so v1 uses typed claim fields.
- **Demo story:** An employee asks 'Can I claim a Rs 3,200 cab from the airport at 2 a.m.?' and gets 'Yes, under clause 6.1, night travel'. He files the claim through the generated form. The pre-check labels it Needs review because it is over the city cap, with reasons, and it reaches Finance admins rather than IT. The approver sees the label, the clauses and the exact record to be written, and approves it. Ramp's 'over 65% decided automatically' is vendor-reported, so present it as industry context only.

### Company Assistant (governed general helper)

- **Department:** All employees
- **Flows:** Drafting, summarising, rewriting and translating pasted text; no company tools; Official in the catalog; per-person monthly cap with a cheaper default model; warning before sending when the message looks like it contains Aadhaar, PAN or card numbers (existing pii.py detector); everything audited and under API no-training terms.
- **Demo story:** An employee pastes meeting notes containing a colleague's PAN number. Before sending, Chat warns: 'This looks like a PAN number, send anyway?' She removes it and gets a clean summary. The admin's value report shows 140 weekly users and spend within the per-person cap. The pitch to leadership: personal ChatGPT use moved onto a governed, audited, budgeted tool, with no new per-seat licence.

## The red team: corrections and what the process missed

- Internal-only deployment reality was never examined, and it blocks several items. The Caddyfile comment says a real domain needs ports 80/443 reachable from the internet for certificates, which contradicts internal-only. Customers need either a DNS-01 challenge (a custom Caddy build with a DNS plugin) or an internal CA pushed to every device, and phones reject untrusted CAs. Google sign-in needs an HTTPS host on a public-suffix domain, not an IP or *.corp/*.local. D12 phone chat and 'approve from email' both assume the device can reach the box, which in practice means VPN or zero-trust access. This needs its own short design note and preflight checks before Phase 8.
- Data egress honesty: every prompt and retrieved chunk goes to Anthropic's API. By default ingest also sends every document's full text to Voyage (KnowledgeBaseNodeData embedder='voyage-3-large', config.py voyage_api_key), and 'contextual retrieval' sends documents to the model at ingest. The 'self-hosted / DPDP-ready' pitch must say this plainly. A cheap 'local embeddings' profile (rag/embedders/local_bge.py and rerankers/local.py exist) should come well before the Bedrock item.
- Uniqueness claims need correcting before any marketing. Copilot Studio now has GA evaluations, Evaluation APIs used for pre-publish checks, Agent Registry admin approval, a quarantine API, usage estimation and test generation from analytics. Open WebUI is free at any size (the 50-user threshold covers branding only, https://docs.openwebui.com/license/), has audit logging, an admin-chat-access switch, and a per-user/group permission preview, which already covers much of the 'view-as' idea. The positioning line 'Open WebUI above 50 users' is wrong.
- Google Workspace buyers already get Gemini bundled at no add-on cost (since January 2025), so 'no per-seat fee vs Copilot' does not apply to them. For that segment the pitch must be actions on their own databases with durable approvals, the governed release gate, and data control, not general chat.
- There is no LICENSE file in the repo root. The 'free core vs competitors' paid tiers' positioning assumes a distribution and licence model nobody has decided. Who are the 'buyers' of an internal-only, two-person product (the team's own employer, or other companies installing it)? That needs answering before the GTM sections mean anything.
- Durable-action safety specifics: Arq retries can run a write twice (needs a state machine, a row lock and max_tries=1). The executor bypasses can_use_tool and the hooks, so guards, D8 identity binding and credential rules must be re-applied. Email approval links must never approve by GET (link scanners prefetch them). The approval card must not show model-authored justification text, which is a prompt-injection-to-admin social engineering path.
- Strict abstain conflicts with SSE streaming: an uncited answer is already on screen before any post-hoc check. Strict mode needs buffering or an 'unverified' streaming state.
- Product audit logs (and the employee 'access to your conversations' panel) cannot see database, host or backup access by operators. Say this explicitly, or consider encrypting conversation bodies at rest with keys the app holds, as a later option.
- Capacity reality: Do next alone adds roughly 35-55 developer-days on top of the roughly 10-week Phases 7-10 plan, and Do soon adds about 14 more items, for a two-person team that also has to support upgrades, DB migrations and backups on installs. Nobody proposed a cut line tied to a named pilot. Validate with 2-3 IT heads (as the synthesis itself suggests for compliance) before committing to anything past Do next.
- Gmail SMTP daily send caps and Workspace admins disabling app passwords were not considered against needs-review notifications, approvals, digests and scorecards. There is no mail queue, rate limit or bounce handling in the plan.
- Shared library implications for the canvas: chunking, embedder and contextual-retrieval settings live on the per-assistant knowledge node today, so a shared library changes the graph spec, the version diff and the setup wizard, not just retrieval.

## All opportunities, ranked by the judges

| Rank | Opportunity | Total (of 30) | Effort |
| --- | --- | --- | --- |
| 1 | Durable admin approvals: writes that wait hours or days without a live turn | 23 | M (1-2 weeks) |
| 2 | From complaint to regression test: thumbs-down becomes an eval case, then 'fixed in v3' | 22 | M (1-2 weeks) |
| 3 | Traffic mode on the canvas: live usage, failures, latency and approvals per node | 22 | S (days) |
| 4 | Approve on evidence: eval scorecard, safety checks and cost forecast on the admin's approval card | 21 | M (1-2 weeks) |
| 5 | Live answer quality: a sampled groundedness judge plus a strict-citation 'abstain' mode | 19 | M (1-2 weeks) |
| 6 | Transparent admin access: required reason, an employee-visible access log, and a tamper-evident audit chain | 19 | M (1-2 weeks) |
| 7 | Hand-off that always works: a summary, routing by topic, and the outcome recorded | 19 | S (days) |
| 8 | Pause, roll back and stage: control a live assistant after approval | 19 | S (days) |
| 9 | Value report per assistant: deflection, cost per resolved question, retention and before/after each version | 18 | M (1-2 weeks) |
| 10 | Slow down instead of stopping: budget-aware routing that steps the model down near the limit | 18 | S (days) |
| 11 | Close the EchoLeak-style data leak in the chat renderer before Chat opens to every employee | 17 | S (days) |
| 12 | Shadow replay: test a new version on last week's real questions before approving it | 17 | M (1-2 weeks) |
| 13 | Source owners, review-by dates, scheduled re-fetch and stale-citation badges | 17 | S (days) |
| 14 | 'View as' permission preview and a PII sensitivity scan at ingest | 17 | M (1-2 weeks) |
| 15 | Action cards and intake forms generated from the tool's input schema | 17 | M (1-2 weeks) |
| 16 | HR policy pack with a 'confidential assistant' mode | 17 | M (1-2 weeks) |
| 17 | Approval policies: routing by group, backup approvers, SLAs and threshold rules | 16 | M (1-2 weeks) |
| 18 | Knowledge gap studio: cluster unanswered questions and draft the missing handbook section | 16 | M (1-2 weeks) |
| 19 | A data dictionary for database connections: business definitions, verified queries, accuracy evals and table output | 16 | M (1-2 weeks) |
| 20 | Scheduled and event-triggered assistant runs: digests, reminders and SLA watchers by email | 16 | M (1-2 weeks) |
| 21 | An approved general 'Company Assistant' to pull usage back from personal ChatGPT | 16 | S (days) |
| 22 | Playbooks: builder-written step-by-step procedures loaded only when needed (Agent Skills) | 15 | M (1-2 weeks) |
| 23 | Sign in with any company identity: generic OIDC with Google, Entra ID, Zoho and Keycloak presets, plus any SMTP | 15 | M (1-2 weeks) |
| 24 | Compliance and policy guidance pack: strict citations, effective dates and a tricky-question eval set | 15 | S (days) |
| 25 | Indian language support: answer-language setting, a Hinglish eval pack, Devanagari fonts and a translate MCP | 14 | M (1-2 weeks) |
| 26 | DPDP and CERT-In pack: retention floors, rights requests, breach export and a governance report per assistant | 14 | M (1-2 weeks) |
| 27 | A security review pack generated from the live install, for the CISO | 14 | M (1-2 weeks) |
| 28 | Support-agent copilot pack: reframe store-support for the company's own support staff | 14 | S (days) |
| 29 | An org knowledge library: sources owned once, with groups, attached to many assistants | 13 | M (1-2 weeks) |
| 30 | Files in a conversation: attach a PDF, DOCX, XLSX or CSV for one chat, not the shared knowledge base | 13 | M (1-2 weeks) |
| 31 | Model inference in India: an Amazon Bedrock provider option and a 'data stays in India' profile | 13 | M (1-2 weeks) |
| 32 | Google Drive connector with scheduled re-sync and Drive sharing mapped to groups | 12 | L (3-6 weeks) |
| 33 | An approved MCP server catalog built from the official MCP Registry | 12 | M (1-2 weeks) |
| 34 | Answer where people already ask: a Google Chat app with approval cards (Teams next) | 12 | L (3-6 weeks) |
| 35 | Finance pack: expense and travel policy Q&A, reimbursement status, and 'pre-check then approve' | 12 | S (days) |
| 36 | Add Opus 5.5 and Sonnet 5.5, with per-model thinking mapping and correct prices | 11 | S (days) |
| 37 | Analyse spreadsheets on-prem: an analyze_file tool running Python in the existing MCP jail | 11 | L (3-6 weeks) |
| 38 | Filled documents back: export answers and reports as DOCX or XLSX from company templates | 11 | M (1-2 weeks) |
| 39 | Wire the unused API tokens: let internal systems call approved assistants, over REST or as an MCP server | 11 | M (1-2 weeks) |
| 40 | Plant and field SOP pack: phone-first, QR-code entry, local-language answers with page citations | 10 | M (1-2 weeks) |

## Research angles

### The most useful internal use cases by department, with named evidence from 2023 to 2026 and how well each fits Assistant Studio, ranked by value times fit

I compared 17 internal use cases across 10 departments against named evidence from 2023 to 2026 and against the code. Back-office work has the strongest evidence and the best fit with what we already have.

The top seven, scored value times fit out of 25:
1. **IT helpdesk (25).** Microsoft: 31% fewer tickets, aiming for 40%. Bank of America: over 90% adoption and more than half fewer service desk calls.
2. **HR policies and requests (20).** IBM AskHR answers 94% of questions without escalation and cut tickets by 75%.
3. **Assist for the company's own support staff (20).** This stays within the internal-only rule. A study of 5,179 support agents found 14% more issues resolved per hour (34% for novices). DoorDash added answer checks plus an LLM judge and cut hallucinations by 90%.
4. **Policy and compliance guidance (20).** Deutsche Bank and TCS: finding a policy went from over a day to seconds.
5. **Company knowledge (20).** Klarna's Kiki: 85% of staff, about 2,000 questions a day. Morgan Stanley: 98% of advisor teams.
6. **Self-serve data questions (15 now, 20 later).** Swiggy's accuracy went from 54% to 93% once it added a data dictionary. Pinterest saw 35% faster SQL writing.
7. **Finance policy and expense pre-checks (16).** Ramp's agent decides over 65% of expense approvals on its own.

Two of our features fit the evidence closely:
- **Evals.** Morgan Stanley credits its eval framework for its adoption. Showing eval results on the admin's approval card would be a real differentiator.
- **Tiered approvals.** Ramp's Approve, Reject or Needs review pattern fits the approval tiers we have planned.

Four gaps hold back most of the remaining use cases:
- **Live connectors and chat-app channels.** Content can only come in as file, url or text today. Klarna, Uber and Swiggy all answer inside Slack, and LinkedIn's data bot got 5-10x more use once it moved into the tool people already used.
- **A data dictionary for each database.** Today the database node stores only the connection and two switches, so the model gets no business definitions.
- **Files in a conversation.** Legal, RFPs and invoices need an attachment in chat and a filled document back. We have no chat attachments, no xlsx or csv parsing, and no document export.
- **Scheduled runs.** KPI digests, reminders and onboarding check-ins need them. The only scheduled job today is the MCP health check.

Plan changes:
- **Conversation privacy.** Add a 'confidential' setting for HR-type assistants. Letting every admin read every conversation would put people off asking sensitive HR questions.
- **Analytics.** Lead with deflection and 'Did this solve it?' rates. Show hours saved only as an estimate with its assumption stated, because published hours-saved figures vary wildly. JPMorgan reports alone range from 1-2 hours a week to 4 hours a day.

**Evidence that is weak or unverified:**
- **Vendor numbers:** Moveworks, RFP tools, procurement, Highspot.
- **Not checked against the original:** McKinsey Lilli, ACG Capsules and Siemens (from secondary roundups); Uber QueryGPT's hours (secondary write-ups); Morgan Stanley (the OpenAI page blocked fetching, so its figures come from search snippets and a secondary write-up).

Code checked:
- apps/api/app/graph/nodes.py
- apps/api/app/schemas/assistant_config.py
- apps/api/app/models/rag.py
- apps/api/app/rag/parsers/dispatch.py
- apps/api/app/worker.py
- apps/api/app/datasources/sql_guard.py
- apps/api/app/evals/gate.py
- docs/INTERNAL_PLAN.md

### New agent capabilities (2025-2026) worth building on: background/scheduled agents, human checkpoints, deep research, structured outputs and forms, document generation, code execution, computer use, voice, memory, A2A, MCP ecosystem, Claude platform features, eval-driven development, observability. Each is mapped to the current architecture (Claude Agent SDK driven through the Claude Code CLI, the sandboxed MCP runner, the Arq worker, detached turns on Redis streams) and to the internal-only plan in docs/INTERNAL_PLAN.md.

The capabilities that best fit Assistant Studio's internal-only direction, and that the current architecture already almost supports, are listed below. The research was read-only.

1. **Durable admin approvals.** The Agent SDK's PreToolUse "defer" decision ends the turn with tool_deferred and resumes it later, with no timeout. It can replace the in-process approval future in approval_registry.py, which times out and denies. Limit: it works only when Claude makes one tool call in the turn.
2. **Scheduled and event-triggered assistants on the Arq worker.** This needs a schedules table plus a once-a-minute tick, because Arq's cron() is fixed when the worker starts. Background runs can write to the same Redis-stream log that detached turns use.
3. **Structured outputs and forms.** The Agent SDK's output_format can produce schema-valid action cards for the employee to check and edit before a write.
4. **File analysis and report generation in a self-hosted sandbox.** This would reuse the jailed MCP runner. Anthropic's code execution tool and pre-built document Skills are GA and cheap (1,550 free container-hours a month, then $0.05 per hour), but they are not covered by zero data retention and keep files for up to 30 days. That conflicts with the self-hosted promise, and the Claude Code CLI cannot be handed API server tools anyway.
5. **Skills as builder-written playbooks.** Each skill costs about 100 tokens in context until it is used. This means changing setting_sources=[] and adding "Skill" to the tools allow-list.
6. **MCP spec and ecosystem.**
   - The 2026-07-28 revision removes protocol sessions and adds multi-round-trip input requests, a tasks extension and trace context in request metadata.
   - MCP Apps let a server show its own interactive UI inside the chat.
   - The official MCP Registry can feed an admin-approved internal catalog of MCP servers.

Lower priority or later:
- Deep research as a budget-gated background job (about 15x the tokens of chat).
- Native search_result citations (needs a spike to confirm the CLI passes them through).
- Memory retention and admin-audited memory reads.
- Batch API at 50% off for the eval judge and digests.
- Turning tool search back on for assistants with many MCP tools.
- OpenTelemetry GenAI span naming.
- A production-to-eval feedback loop shown on the approval screen.
- Speech-to-text for phone chat (e.g. Sarvam, about Rs 30 per hour of audio, Indian languages).
- Computer use and A2A: mature, but not worth building now.

One immediate maintenance issue: apps/api/app/agent/models.py lists only Opus 5 and Sonnet 5. Opus 5.5 is cheaper ($4/$20 per million tokens versus $5/$25). Opus 5.5 rejects disabled thinking and Sonnet 5.5 needs between_tools instead, while options.py sends {"type":"disabled"} whenever adaptive thinking is off. So the model update needs a per-model mapping and a test.

Unverified, needs checking:
- Whether the CLI passes search_result blocks from tool results through to the API.
- Which MCP protocol version the pinned MCP SDK speaks.
- Whether the CLI already translates the thinking option for the 5.5 models.
- Voice-stack details from secondary sources.

Sources: official Anthropic docs (platform.claude.com, code.claude.com, anthropic.com/engineering), modelcontextprotocol.io, a2a-protocol.org, arq docs, OpenTelemetry semantic-conventions-genai and Sarvam pricing. Code references: apps/api/app/agent/options.py, approval_registry.py, caps_memory.py, citations.py and models.py, apps/api/app/worker.py, apps/api/app/services/turns.py, apps/api/app/mcp/runner.py, apps/api/app/evals/judge.py, docs/INTERNAL_PLAN.md and docs/PRD.md.

### Self-hosted and open-source competitors (Dify, Flowise, Langflow, n8n, Open WebUI, LibreChat, AnythingLLM, Onyx, Botpress, Rasa, Haystack/deepset, LobeChat, plus newcomers Coze Studio, Sim, Pinchy, and OpenAI AgentKit), with Microsoft Copilot Studio as the incumbent. For each: positioning, strengths, weaknesses, licensing and enterprise gating. Then the white space Assistant Studio can own, the table-stakes gaps, and what would be genuinely unique.

The self-hosted field splits into four groups: visual builders (Dify, Flowise, Langflow, n8n, Sim, Coze Studio), chat front doors (Open WebUI, LibreChat, LobeChat, AnythingLLM), permission-aware knowledge search (Onyx), and conversational-AI vendors that left free self-hosting (Botpress cloud-only, Rasa capped at 100 internal conversations a month, deepset with custom pricing). Copilot Studio is the incumbent, and almost free inside Microsoft 365 Copilot companies. Three facts shape our strategy. First, the features internal IT needs (SSO/SCIM, RBAC, audit, permission sync, evals, chat-only roles) are paid in nearly every open-core competitor: Dify Enterprise, Flowise Enterprise, Onyx EE, n8n Pro/Business, and Open WebUI's licence above 50 users. Second, the builder leaders have serious security and permission problems: Langflow and Flowise had exploited remote-code-execution flaws, two Langflow ones on CISA's exploited list, and Dify's open-source RBAC has documented bugs. Third, the canvas itself is not a moat: OpenAI is closing Agent Builder and Evals on 30 November 2026, and n8n Chat Hub (January 2026) already offers our planned Studio-plus-Chat split. The white space is a free, self-hosted, governed internal platform: an assistant approval gate with the eval scorecard attached, risk-tiered action approvals bound to the asker's identity with full audit, admin reads that are audited and visible to employees, budgets in local currency, a security-first runtime, and portability by exporting to config or code. It suits companies on Google Workspace or outside Microsoft, and could be tied to India's DPDP deadlines (13 Nov 2026 and 13 May 2027; buyer pull unverified). Table stakes we lack, by priority: live connectors with scheduled re-sync (start with Google Drive mapped to our groups), generic OIDC beyond Google, publishing an assistant as MCP or API, and later Slack or Google Chat channels. Staying Claude-only is defensible, but expect to defend it. Star counts, Sim's funding and Dify's enterprise price come from third-party sources and are unverified.

### Real pain with internal AI assistants (employees, IT admins, leaders): hallucination and trust, stale knowledge, permission leaks, low adoption after launch, unclear ROI, cost overruns, shadow AI, no feedback loop, pilot purgatory, change management. For each pain: the evidence, what worked elsewhere, and what it means for Assistant Studio, checked against the repo (read-only).

Across MIT NANDA, Gartner, McKinsey, BCG, S&P, KPMG, Workday and IBM, plus incident write-ups and vendor case studies, the main complaints about internal AI assistants are: no provable ROI (pilot purgatory); tools that don't learn from correction; usage that drops after launch; shadow AI filling the gap that generic work leaves; oversharing and prompt-injection leaks; confident hallucinations that employees don't check; stale knowledge; cost forecasts off by 5-10x; and underinvestment in people and process. MIT's 95% figure is contested, so treat it as direction, not a measurement. What worked publicly: evals before launch and constant iteration (Morgan Stanley 20%→80% recall, 98% adoption); a live guardrail and judge (DoorDash, about 90% fewer hallucinations); verification owners and intervals (Guru); mining unanswered questions into new articles (Moveworks; Databricks 10%→73% deflection, vendor-reported); living in the chat tool employees already use (Klarna Kiki, about 85% adoption); staged, permission-cleaned rollouts. For Assistant Studio, the most distinctive ideas build on what already exists: (1) one loop from thumbs-down to eval case to regression gate to 'fixed in vN'; (2) ROI per assistant, with cost forecast and cost per resolved question, on the approval card and in analytics; (3) a sampled live groundedness judge with an abstain path; (4) source owners, review dates and scheduled re-fetch moved earlier than 10.4; (5) an approved general 'Company Assistant' to pull usage back from shadow AI; (6) 'View as' plus a PII and Indian-ID sensitivity scan at ingest; (7) an employee-visible log of who read their conversations, plus DPDP-ready retention. One concrete risk found read-only (not exploit-tested): apps/web/components/chat/Markdown.tsx renders remote images from model output, and the CSP in apps/web/next.config.ts has no img-src. That is the same data-leak sink EchoLeak used, and it should be closed before Chat opens to all employees. Reddit was not reachable from the research tools, so community evidence relies on HN comments (anecdotal) and secondary reporting; vendor-reported numbers are marked as such.

### The Indian market for a self-hosted internal AI assistant platform: who buys, which suites they run, languages, DPDP/sector regulation, pricing, competitors/partners, channels and procurement, and what each means for Assistant Studio.

India suits Assistant Studio's direction (self-hosted, internal only, approval gates, audit), but three gaps stand out.

1. Model inference still leaves India. AWS made Claude Opus 5, Sonnet 5 and Haiku 4.5 available in-country through Bedrock (Mumbai and Hyderabad) on 29 Sep 2026. The highest-leverage India feature is a Bedrock India provider option (wired through apps/api/app/agent/options.py), plus a 'data stays in India' profile using local bge-m3 embeddings.

2. Sign-in is Google-only. Microsoft dominates large enterprises and IT services, and Zoho matters for SMBs and government. Plan D9 should become generic OIDC with Google, Entra and Zoho presets, and email should work with any SMTP server.

3. Language is English-only. Real employees type Hinglish and regional languages. Integrate Sarvam, IndicTrans2 or Bhashini translation and Indic OCR as tools or MCP servers, and add a Hinglish eval pack.

Regulation is a selling point, not just a burden. The DPDP Rules apply core duties from 13 May 2027 (one-year logs, 72-hour breach reports, penalties up to Rs 250 crore). CERT-In requires 180 days of in-country logs. RBI FREE-AI and SEBI's AI and cloud rules ask for auditability and in-India data, and SEBI proposes lighter rules for internal uses. Retention, erase/export, audited admin reads with reasons, and a per-assistant governance report would make us a compliance accelerator.

Best early buyers:
- Mid-size GCCs and IT/BFSI firms, who value self-hosting and AWS Mumbai.
- Manufacturing and pharma, for multilingual SOP Q&A.
- Government and PSUs later, via GeM startup exemptions. They will want fully local models and security audits.

On pricing, sell 'no per-seat fee, capped per-person spend' against Copilot's Rs 2,495 per user per month. Price in INR, and win on time-to-first-assistant through templates.

On channels: phone web app first, then Google Chat and Teams. WhatsApp is valuable for frontline workers but limited by Meta's Jan 2026 ban on general-purpose AI bots and by per-message costs.

Items marked unverified or low-confidence include secondary pricing and market-share figures, WhatsApp rates, Bedrock India pricing, and whether WhatsApp's policy applies to internal employee bots.

### Our own strengths: what Assistant Studio already does that is rare, and which extensions the existing code makes cheap (with file paths and effort estimates)

I read the code (read-only) and checked a few outside sources. Assistant Studio has three strengths that are uncommon together:

- **The canvas is the config.** The visual canvas compiles to the assistant's configuration and back without losing anything (`apps/api/app/graph/compile.py` and `project.py`). The canvas node component (`StudioNode.tsx`) can already show lit or dimmed routes, time spent per step, version differences and problem lamps.
- **Database writes are classified by parsing, and approved on the exact statement.** Each SQL statement is parsed to decide its risk (`sql_guard.py`), credentials are encrypted with a key that can be rotated, and the reviewer sees the statement that will actually run.
- **Guardrails and a sandbox are built in.** Prompt-injection and personal-data findings are saved and shown on each answer, and local MCP servers run in a jail.

Most of the high-value extensions reuse data and functions we already have:

- **Live traffic on the canvas** (about 5 days): the tool-call records plus the existing mapping from tool calls to canvas nodes (`trace_nodes.nodes_for_tool`).
- **Analytics per version** (about 7 days): runs already record which version answered.
- **Unanswered questions** (2-3 days): the knowledge search returns a fixed "No relevant results" message we can count, and one button can turn a question into a test case.
- **Approval gated on test results** (3-5 days): the existing regression check (`evals/gate.regressions`) and version diff give the admin one review screen.
- **Safety tests for approvals** (1-2 days): prove in every build that a write always asks for approval.
- **Scheduled runs** (5-7 days): the background worker already runs unattended turns for evals and has timed jobs.
- **Shadow replay** (4-6 days): run last week's real questions through a new version and compare the answers.
- **Template gallery** (5-7 days): samples already carry their graph, documents and test suite.
- **Smaller ones:** budget-aware routing, AI-written test questions, a source freshness check, a security feed for admins, and the API tokens table that exists but is unused.

Two findings need decisions before Phase 9:

- **Admin approvals cannot wait long today.** Approvals are in-memory waits with a 300-second timeout, and the saved approval input is redacted. An admin who answers an hour later finds the turn gone. Phase 9.3 needs a lasting "submitted, waiting for an admin" action that a background job runs after approval (about 8-10 days). The same base can grow into a general workflow engine for internal requests. Claude Code's "defer" option might also work, but I could not confirm it works through our SDK version.
- **Knowledge belongs to one assistant.** Each document is stored and embedded per assistant, so decide on a shared library with group permissions before adding groups to sources in 9.1.

Effort is in developer-days for our two-person team and is my estimate.

