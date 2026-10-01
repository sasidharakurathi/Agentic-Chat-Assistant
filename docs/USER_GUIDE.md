# User guide

For the person building assistants in Assistant Studio. Running the server
is in [`OPERATIONS.md`](OPERATIONS.md).

An **assistant** is a chat agent plus whatever you connect to it:
documents to search, databases to query, tools to use, other helpers to
hand work to, and rules for what it must not do. You build one by drawing
those connections, try it in a chat, and publish it when it behaves.

Sign in and create your account on a computer: the builder is a canvas
that needs a larger screen, and on a phone the sign-in and sign-up pages
say so instead of showing their forms.

## 1. The quickest start: a sample

On the **Assistants** page, under your own assistants, **Start from a
sample** lists three finished ones. **Use sample** makes a new draft of
your own and opens it on the canvas.

| Sample | What it shows | Works as it is? |
| --- | --- | --- |
| Store support desk | A knowledge base with four short policy documents, answers with citations, rules | Yes, once its documents finish indexing (a few seconds) |
| Team notebook | Memory across conversations, plus a calculator and a clock | Yes |
| Research desk | Web search, a research subagent, a router, a calculator and a clock | Web search is off when the server runs offline |

Each comes with a few test questions under **Evals** (section 8).
Everything about a sample can be changed; it is an ordinary draft.

The other two ways to start are on the same page: **Guided setup** asks a
few questions and wires up a starter pipeline, and typing a name and
pressing **Create** gives you the basic one: the input, the guardrails,
the agent, and the output.

## 2. The builder

Opening an assistant shows the builder. Along the top: its name, whether
your changes are **Saved**, a **Chat** link, and **Publish**. Below that,
the views:

| View | What it is for |
| --- | --- |
| **Canvas** | The assistant as a line diagram. Most building happens here. |
| **Settings** | The same settings as forms, for when you know what you want to change. |
| **Sources** | The documents the knowledge base searches. |
| **Databases** | Connections the assistant may query. |
| **MCP servers** | External tool servers. |
| **Compiled config** | The configuration the canvas produces. Read-only; useful when something puzzles you. |
| **History** | Published versions, and what changed between any two. |
| **Evals** | Test questions, and how each version did on them. |

Canvas and Settings are two views of one thing. Change either and the
other follows. Changes save by themselves.

## 3. Building an assistant on the canvas

### Reading it

The canvas is drawn like a transit map.

- **The main line** runs left to right in ink, and its stations are
  numbered: **Input**, then **Guardrails**, then the **Agent**, then
  **Output**. A message travels along it.
- **Branch lines** join the Agent, in three colours:
  - **Knowledge** (petrol blue): the knowledge base, its sources, memory.
  - **Actions** (magenta): tools, databases, MCP servers.
  - **Helpers** (violet): subagents and the router.
- A **lamp** on a station shows its state: fine, needs attention, or
  switched off. A hollow bullet means switched off.
- The **Key**, bottom left, lists the colours and what each means.

What is drawn is what the assistant can do. If a database is on the
canvas and connected to the Agent, the agent can query it. If it is not
connected, it cannot, whatever the settings say.

### Adding something

1. Press **Add capability** (top left). The list is grouped: Knowledge,
   Data sources, Databases, Tools, MCP servers, Subagents, Pipeline.
2. Pick one. It appears on the canvas, not yet connected.
3. **Connect it:** drag from the small handle on its edge to the Agent's.
   The line takes the colour of its family. The canvas only lets you draw
   connections that mean something.

Things that must exist first are listed only once they do: a database
appears under Databases after you add its connection in the **Databases**
view, a document under Data sources after you add it in **Sources**, an
MCP server after you register it in **MCP servers**.

### Changing something

Click a station. A drawer opens on the right with that station's
settings: the Agent's instructions and model, a tool's options, a
database's permission to write. Changes save as you make them.
**Remove from canvas** at the bottom of the drawer takes the station off;
for a database or a document, that removes the assistant's access to it,
not the thing itself.

### Without a mouse

Everything on the canvas can be done from the keyboard.

- **Tab** moves through the stations in the order the diagram reads: the
  main line first, then the rest column by column. **Enter** opens the
  station's drawer.
- The **arrow keys** then move the station, one grid step at a time; its
  new place is saved. **Delete** removes it.
- **Connecting**: at the bottom of every drawer, **Connections** lists
  what the station goes to and comes from, each with a **Disconnect**,
  and a **Connect to** list of what it may be connected to. These are
  the same choices a drag would be allowed to make.
- A connection can also be reached with Tab, selected with Enter and
  removed with Delete.

A screen reader hears each station's name, its kind, its step on the main
line and what is wrong with it, and is told when something is connected,
disconnected or removed.

### Who may use what

A capability can be connected to the Agent, to a subagent, or to both.

- Connected to the **Agent**: the agent uses it directly.
- Connected to a **subagent** only: the agent has to hand the work to that
  subagent, which keeps the agent's own context small. The Research desk
  sample does this with web search.

### Keeping it tidy

**Tidy up** (top right) arranges every station into columns by kind, with
the main line on one row. **Suggest a pipeline** lets you describe the
assistant in a sentence and proposes a whole pipeline to look over before
you accept any of it.

### Problems

When something is wrong, a **Problems** list is docked at the bottom of
the canvas. Each entry names the station and says what is wrong in plain
words. Two kinds:

- **Problem**: the assistant cannot be published until it is fixed (a
  capability wired to nothing, a database that no longer exists).
- **Check this**: it will work, and probably not as you meant (a subagent
  with nothing to use).

Most entries have a one-click fix next to them, labelled with what it
does ("Wire it to the agent", "Remove this duplicate"). Clicking an entry
takes you to the station.

## 4. What you can connect

### A knowledge base

For answering from your own documents.

1. **Sources** view: upload files, add a web page, or paste text. Each is
   indexed in the background; its row shows progress and then **Ready**.
2. On the canvas, add **Knowledge base** and connect it to the Agent.
   With no particular sources wired to it, it searches all of them. Wire
   specific sources to it to limit it to those.

Answers then carry numbered citations, and the sources panel beside the
chat shows the passage each one came from. Turn citations off on the
**Output** station if you do not want them.

### A database

1. **Databases** view: add a connection (Postgres, MySQL, SQLite or
   MongoDB). Give it a database user that can only do what the assistant
   should: see [`DATABASE_ACCESS.md`](DATABASE_ACCESS.md). The password is
   stored encrypted and never shown again.
2. On the canvas, add it from **Add capability › Databases** and connect
   it.

By default the assistant can only read. Allowing writes is a switch on
the database's station, and even then every write stops and asks a person
first (section 6).

### Tools

Web search, HTTP requests to sites you list, a calculator, and the date
and time. Each is a station with its own options: how many searches per
answer, which domains, whether a request needs approval.

### MCP servers

An MCP server is someone else's set of tools (a ticket system, a code
host). Register it in **MCP servers**, check that it connects, choose
which of its tools the assistant may use, then add it to the canvas.
Tools that change things ask for approval unless you say otherwise.

### Subagents and the router

- A **subagent** is a helper with a narrower job: searching the knowledge
  base, writing SQL, researching on the web. The agent hands it a task
  and gets back a summary.
- The **router** looks at each message first and decides how much effort
  it deserves, so a greeting is not treated like an analysis.

### Guardrails and memory

Both are stations on every assistant.

- **Guardrails**: rules in your own words ("only answer questions about
  our products"), and the safety checks: spotting instructions hidden in
  documents or web pages, keeping personal data out of web searches.
- **Memory**: whether a conversation's history is kept, when a long one
  is summarised, and whether the assistant may keep notes about a person
  from one conversation to the next.

## 5. Trying it: the chat

**Chat** opens a conversation with the draft. What you change on the
canvas applies to your next message.

- The answer appears as it is written. **Stop** ends it, and is the
  only thing that does: an answer carries on if you reload the page, open
  another conversation, or lose the connection. Come back and it picks up
  where it is, or shows the finished answer. A conversation that is
  answering has a green lamp in the list, so several can be running at
  once. One conversation answers one message at a time.
- Each tool the assistant uses shows as a card: what it called, with
  what, and what came back.
- Under each answer, **Run details** opens the run: every step in order,
  how long each took, what it cost. **Show on canvas** lights up the
  stations that run went through, which is the fastest way to see why an
  answer came out as it did.
- Type `/` for commands: `/new`, `/retry`, `/rename`, `/archive`,
  `/cost`, `/export` (the conversation as Markdown), `/memory` (what the
  assistant remembers about you, and forgetting it), `/help`.

## 6. Approvals

When the assistant wants to do something that changes data (write to a
database, send a request, use a tool you marked as needing approval), the
turn pauses and a card appears in the chat.

The card shows **exactly** what will run: the SQL statement itself, the
request's address and body, never a summary. **Deny** is the default.
**Approve** lets that one call through. If nobody answers in five
minutes, it is denied.

Only the person in the conversation, or an admin, can decide.

## 7. Publishing, and going back

A draft is yours to break. People using the assistant get the last
**published** version.

- **Publish** saves the current draft as a new numbered version. You can
  add a note about what changed. Publishing is refused while the Problems
  list has a Problem in it.
- **History** lists every version. Pick two to see what differs. The
  two pipelines are drawn as one: stations tagged **Added**, **Removed**
  or **Changed**, an added connection on a green band, a removed one
  dashed red, and everything that stayed the same in grey. Below the
  drawing are the same changes as a list, and every setting that changed
  with its value before and after. Pick a station on the drawing to see
  only its settings.
- A version never changes. There is no one-click way back yet: use the
  comparison to see what changed since the good version, undo that on
  the canvas, and publish again.

## 8. Evals: does it still work?

An eval suite is a list of questions with what a good answer looks like.
Running it asks every question for real and scores the answers, so you
find out that a change broke something before your users do.

1. **Evals** view › create a suite, then **Add a case**: the question,
   and any of:
   - words the answer must contain, or must not;
   - a tool it must have used;
   - that it must cite a source;
   - which source should have been found (this scores the search
     separately from the answer);
   - a reference answer, for the judge.
2. **Run against** the draft or a published version.
3. The run shows, per case, what was asked, what came back, and which
   checks passed. **Compare with** an earlier run to see what got better
   and what got worse.

**Import a file** takes many cases at once (CSV or JSON).

On a server using the real model, a judge model also grades each answer
for correctness and tone. Each case is a real turn, so a run costs what
that many chats would.

## 9. Costs and limits

The **Usage** page shows what has been spent, by assistant, by model and
by conversation.

**Budgets** set a limit per day or month, for the whole organisation or
for one assistant. At 80% chats show a warning; at 100% they stop until
the period ends or an admin raises the limit. A single conversation can
also be capped in the Agent's settings.

## 10. People

**Members** lists who is in the organisation. **Create invite link**
makes a link for one email address and one role; send it to them.

| Role | Can |
| --- | --- |
| Member | chat with assistants; build and change their own |
| Admin | everything a member can, plus: change any assistant, invite people, set budgets, add local-command MCP servers, decide any approval, change the role of members |
| Owner | everything an admin can, plus: change anyone's role |

An admin can invite members and admins, not owners.

## 11. When it does not do what you expect

| What you see | Look at |
| --- | --- |
| It ignores a document you added | **Sources**: is it **Ready**? Canvas: is the knowledge base connected to the Agent? |
| It says it cannot query the database | Is the database connected on the canvas? Does the database user have permission? |
| It answers from general knowledge, not your documents | **Run details** › was the knowledge base searched? If not, say so in the Agent's instructions |
| An answer is wrong | **Run details** › **Show on canvas**: what it searched, what it found, which tool said what |
| "This conversation has reached its spend limit" | the conversation's cap, or a budget, on **Usage** |
| "Too many conversations are running right now" | the server is busy; try again in a moment |
| Publish is greyed out | the **Problems** list |
| It used to work | **History**: compare the current version with the last good one; then run the eval suite against both |
