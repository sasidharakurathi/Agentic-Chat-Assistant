# Where data goes

Assistant Studio runs on your own server. Some data leaves it to reach
services outside the building. This page lists each one (plan D18,
Phase 8.7): what it receives, when, and how to turn it off. The privacy
notice that everyone accepts before their first message in Chat builds
its "Where your messages go" list from the same settings
(`app/services/privacy.py`), and each assistant's card (**About**) lists
what that assistant sends out.

The terms below are as the gap review found them (2026). Check each
provider's current terms before the first employee is invited, and again
when they change.

| Service | Receives | When | Turn it off |
| --- | --- | --- | --- |
| **Anthropic** (the model, United States) | Each message, the conversation so far, the system prompt, and what the assistant looks up for it (retrieved passages, database rows, tool results) | Every answer, evals, the AI helpers, titles and summaries | Can't be turned off: it writes the answers. Under commercial terms with a DPA; API data is deleted within 30 days by default (longer if flagged under the usage policy). |
| **Voyage AI** (embeddings and reranking) | The full text of documents when they are indexed, and each question that searches a knowledge base | Ingest, and every knowledge search | `RAG_OFFLINE=1` (local embeddings and reranking; switching means re-indexing). Voyage's default terms reportedly allow training on what it receives unless the organisation opts out, which covers only data sent after. Decide before the first ingest. |
| **Web search** (through Anthropic's server tool) | Searches the model writes from people's questions | Only assistants with web search on | Off per assistant, on its card |
| **Sites an assistant reads** (HTTP tool, URL sources) | The requests it makes, to the allowed domains only | Only assistants that have them | Per assistant |
| **Remote MCP servers** | What the assistant sends each tool | Only assistants that use one | Per assistant |
| **Company systems** (HTTP connections, Phase 9.3) | The values of each declared call, and, when the connection says who is asking, the person's email, name and id; only to that system's host, which is usually inside the building (an internal destination) | Only assistants with calls on a connection | Per assistant; the owner's **Internal destinations** decide which internal systems can be reached at all |
| **Google** (sign-in) | Who you are: the account's id, email and domain | Signing in with Google | Leave `GOOGLE_CLIENT_ID` empty |
| **Your mail server** (Gmail SMTP, or another) | Invites, review and approval notifications, password links. Never what anyone wrote in a conversation | Each email | Leave `SMTP_HOST` empty (`MAIL_BACKEND=log`) |
| **Langfuse** (tracing) | A trace of each answer: ids, model, tokens, cost, tool names and timings; messages and answers too only with `TRACE_CONTENT=full` (off by default in production) | Only when `LANGFUSE_HOST` is set | Leave it empty, or self-host it |

Nothing else calls out. The server's own logs, backups and the database
stay on the machine; whoever runs the server can read them, and those
reads are outside the app's audit log.

## Inside the building

- **Knowledge for some people only** (Phase 9.4): a source given to
  groups answers only them, and the assistant is offered only to people
  who can read all of it.
- **Screenshots** you send stay with the conversation in the server's own
  storage, go to the model with your message, and are erased with the
  conversation.
- **A hand-off** emails the contact only a link; its summary stays in the
  app. A ticket action sends the summary to that one system.
- **Analytics** count conversations and verdicts; builders see no names.
- **Shared conversations** open only inside the organisation, for people
  who may use the assistant and read what it cites; each opening is
  recorded, and the link can be revoked.

- **Admins and the owner can open any conversation.** Each opening needs a
  reason and is recorded first (Phase 7b.2). The person sees each one under
  **Privacy & help › Access to your conversations** (Phase 8.7).
- **Usage is recorded per person**, for budgets.
- **What an assistant may do as you** is on its card: the lookups, and
  the changes you confirm or an approver decides (Phase 9.1).
- **A request that waits** keeps exactly what it would do (sealed with
  the server's key) until it runs, is declined or expires, then only a
  redacted copy and what happened. Approvers see the request, not the
  conversation; emails about it carry its title and reference, never the
  details.
- **A database scoped to you** (Phase 9.3) is told your email with each
  statement, so its own rules show you only your rows.

## Asking about your data

The privacy notice names the organisation's privacy contact (organisation
settings; the owner when empty). The owner can erase one person's data
(Phase 8.10); conversations are erased for good after the organisation's
retention period (365 days by default). Exporting one person's data comes
later.
