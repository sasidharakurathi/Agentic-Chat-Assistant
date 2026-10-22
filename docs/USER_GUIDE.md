# User guide

For the person building assistants in Assistant Studio. Running the server
is in [`OPERATIONS.md`](OPERATIONS.md).

An **assistant** is a chat agent plus whatever you connect to it:
documents to search, databases to query, tools to use, other helpers to
hand work to, and rules for what it must not do. You build one by drawing
those connections, try it in a preview, and submit it for review when it
behaves. An admin approves it before anyone else can chat with it.

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
your changes are **Saved**, where it stands in Chat (**Not approved yet**,
**Live** or **Paused**), a **Preview** link, and **Submit for review**.
Below that, the views:

| View | What it is for |
| --- | --- |
| **Canvas** | The assistant as a line diagram. Most building happens here. |
| **Settings** | The same settings as forms, for when you know what you want to change. |
| **Sources** | The documents the knowledge base searches. |
| **Databases** | Connections the assistant may query. |
| **MCP servers** | External tool servers. |
| **Compiled config** | The configuration the canvas produces. Read-only; useful when something puzzles you. |
| **History** | Versions and their review, and what changed between any two. |
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

- **Problem**: the assistant cannot be submitted until it is fixed (a
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

**Who can read a source.** Each source is everyone's, or only for groups
(**Change** on its row). Answers and citations use it only for people who
can read it, and the assistant is offered in Chat only to people who can
read all of its restricted sources. With no groups chosen, nobody can read
it.

**"Waiting for you".** A source open to everyone that contains a password
or key, or several Aadhaar, PAN or card numbers, isn't indexed until you
decide. The row shows what was found, masked. **Choose groups**, or
**Everyone may read it** (recorded).

**Getting help from a person** (Chat › **Get help from a person**, when
the assistant has hand-off on). It sends the contact a link, or opens a
ticket, with a short summary kept in the app. **Screenshots**: attach or
paste them. A password or key you type is hidden before it's sent.
**Export** downloads the conversation as Markdown.

**Sharing a conversation** (Chat › **Share**). Share makes a read-only
link, shown once. It opens for colleagues who can use the assistant and
read what it cites. Revoke it any time; it also stops if you leave.

### A database

1. **Databases** view: add a connection (Postgres, MySQL, SQLite or
   MongoDB). Give it a database user that can only do what the assistant
   should: see [`DATABASE_ACCESS.md`](DATABASE_ACCESS.md). The password is
   stored encrypted and never shown again.
2. On the canvas, add it from **Add capability › Databases** and connect
   it.

By default the assistant can only read. Allowing writes is a switch on
the database's station, and even then every write waits for an approver
(section 6).

**Actions** (on the database's drawer) are better for anything people
ask for often: a statement you write once, with the values the assistant
fills in (`:app`, `:reason`) and who is asking filled in for you
(`:asker_email`, `:asker_name`, `:asker_id`). Each one runs at once (for
lookups), once the person asking confirms it (only for their own rows:
`... WHERE id = :id AND requester_email = :asker_email`), or once an
approver decides. The drawer checks each before saving and says what to
fix. An admin approves them with the version.

**Each person sees only their own rows** (Postgres): turn on **Answer
only for the person asking** in the connection's permissions. Every
statement then tells the database who is asking, and its row-level
security policies decide what they see. Your database admin sets those
up (OPERATIONS, "Company systems"). **Test connection** says if they
won't apply, and lists tables that everyone sees in full.

### Tools

Web search, HTTP requests to sites you list, a calculator, and the date
and time. Each is a station with its own options: how many searches per
answer, which domains, whether a request needs approval.

**Company systems** (ticketing, HR, inventory) are reached from the HTTP
request station's drawer:

- **Connections**: the system's address and the headers it needs, such
  as an API key. They are stored sealed, and only their names are shown
  again. **Say who is asking** sends the person's email, name and id to
  that system only, so it can answer as them.
- **Calls**: one named request on a connection, such as
  `POST /tickets` or `GET /tickets/{ticket_id}`, with the values the
  assistant fills in. Who is asking can go in the path as
  `{asker_email}`. A GET runs at once. Anything else waits for the person
  asking to confirm (the connection must say who is asking) or for an
  approver. You can name the fields of the answer the assistant reads,
  so nothing else the system returns reaches it.

A system on the office network needs the owner to list it first, under
the organisation's **Internal destinations**.

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
  where it is, or shows the finished answer. If the connection drops
  while you watch, it says **Reconnecting…** and carries on from where it
  was. A message that couldn't be sent goes back in the box, and **Try
  again** waits when the server says how long. A conversation that is
  answering has a green lamp in the list, so several can be running at
  once. One conversation answers one message at a time.
- Each tool the assistant uses shows as a card: what it called, with
  what, and what came back.
- Under each answer, **Run details** opens the run: every step in order,
  how long each took, what it cost. **Show on canvas** lights up the
  stations that run went through, which is the fastest way to see why an
  answer came out as it did.
- Under each answer, a **thumbs up or down**, and a comment if you want:
  it goes to the assistant's team with that question and answer, without
  your name. Under the latest answer, **Did this solve it?**
- Type `/` for commands: `/new`, `/retry`, `/rename`, `/archive` (removes it from your history),
  `/cost`, `/export` (the conversation as Markdown), `/memory` (what the
  assistant remembers about you, and forgetting it), `/help`.

## 6. Approvals

When the assistant wants to change something (write to a database, send
a request, use a tool that needs approval), it doesn't do it there and
then: it saves a **request** and says so, and a card shows where it
stands. Nothing runs until someone decides.

- **Your own** (an action on your own rows, or anything while you
  preview your draft): the card shows the details, which you can correct,
  with **Confirm** and **Don't do it**. It waits a day.
- **An approver's**: everything else. The card says it waits for an
  approver, with **Withdraw**. The approvers are emailed; they decide in
  **Approvals**, where they see exactly what would run (the statement
  itself, never a summary). It waits three days by default, and they are
  reminded once.

Whoever decides, it then runs once, after checking it is still allowed
(the assistant isn't paused, you still have access), and how it went
appears in the conversation; for an approver's, you are emailed too.
Nobody decides their own approver request. An admin chooses who approves
each assistant's requests (History › **Who approves its requests**);
nobody named means the admins.

### The listing: who it is for, and how Chat shows it

The builder's **Listing** tab sets a **category**, up to four
**conversation starters** (questions offered in a new conversation), and
**who it is for**: everyone, members of groups, or named people. Admins,
the owner and the assistant's builders always see it. Saving changes
nothing in Chat: the listing goes with the next version you submit, and
the admin reviewing sees it. Group membership changes reach Chat at once.

**Groups** (admins, in the sidebar): make a group, add and remove people.
A group an assistant is for can't be deleted until its audience changes.

**Official** (admins, in History): an assistant the company vouches for,
shown first in Chat with a badge. Chat's list can be searched and
narrowed by category.

## 7. Review, going live, and going back

A draft is yours to break. Nobody but you and the admins can chat with
it: people using the assistant get the version an admin **approved**.

- **Preview** opens a test chat on the draft as it is now. Only the
  assistant's builder and the admins see previews, and they are kept out
  of the normal chat list. Writes the assistant asks for in a preview
  still wait for approval.
- **Submit for review** freezes the draft as a new numbered version, with
  a note about what changed for the reviewer. Submitting is refused while
  the Problems list has a Problem in it. A newer submission replaces one
  still waiting.
- **An admin reviews it** in **History**: **Compare with the live
  version** shows what changed in the pipeline and settings, and in what
  it uses (a knowledge source with new content, a database connection
  that now allows writes, a tool whose description changed). **Preview**
  on the version chats with exactly what was submitted. Then **Approve**
  (with an optional note) or **Reject** (with a reason you will see). Nobody
  approves their own work: if you submitted a version or built the
  assistant, another admin or the owner approves it. An owner with nobody
  else to ask approves their own, with a note saying why it is ready.
  You can **Withdraw** your own submission before it is reviewed.
- **Approving makes it live** for new conversations. A conversation
  already going stays on the version it started on.
- **What was approved stays as it was.** Reindexing a document after
  approval gives you a new copy to preview; Chat keeps answering from the
  approved copy until the next approved version. A database connection,
  MCP server or tool changed after approval is left out of the live
  version's answers until a new version is approved, and History says
  so. A knowledge source the live version answers from can't be deleted.
- **Going back:** an admin can **Roll back** to the previous approved
  version from History. The version rolled back from stops answering, and
  its conversations move to the live one.
- **Pausing:** an admin can **Pause** an assistant. Nothing answers,
  previews included, until they **Reopen** it.

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
2. **Run against** the draft or a submitted version (a run on the
   submitted version is evidence for its review).
3. The run shows, per case, what was asked, what came back, and which
   checks passed. **Compare with** an earlier run to see what got better
   and what got worse.

**Import a file** takes many cases at once (CSV or JSON).

On a server using the real model, a judge model also grades each answer
for correctness and tone. Each case is a real turn, so a run costs what
that many chats would.

## 9. Costs and limits

**Limits for each person** (admins, Usage): a daily or monthly limit for
each Employee and one for each builder, admin and the owner, and a
person's own in place of their role's. Reaching it stops only that
person; Employees are told when most of it is used and when it is back,
never in money. Only admins see one person's spend.

**Alerts** (admins): when something stops Chat for everyone (the model
provider's limit, the organisation's budget, the server at capacity),
admins are emailed, see a banner, and find it on the Alerts page. It clears
itself when answers work again, or **Mark handled**.

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
| Employee | chat with approved assistants (their conversations are private). Their requests that need approval wait for an admin. Answers come without the working parts: which tool was used, not what it ran or returned |
| Builder (member) | everything an employee can, plus: build, preview and submit their own assistants, and approve requests in their own conversations |
| Admin | everything a member can, plus: open anyone's conversation, giving a reason that is recorded; approve or reject versions, roll back, pause and reopen assistants, change any assistant, invite people, set budgets, add local-command MCP servers, decide any approval, change the role of members |
| Owner | everything an admin can, plus: change anyone's role, and remove admins |

An admin can invite employees, builders and admins, not owners. An
invite makes an **Employee** unless you choose otherwise: most people
only need Chat.

**Chat** (in the sidebar) lists the assistants that answer and your own
conversations. Employees see only Chat.

**Approvals**: the requests waiting for you to decide, as an admin or as
an assistant's approver, each with who asked, what would run, and when it
closes; the sidebar shows how many. Declining needs a note for the person
who asked. **Decided by you** lists what you decided, and admins see
**All requests**. Each request has its own page, where emails link; an
email never decides anything. If several requests arrive close together,
you get one email for them. Away for a while? Your account › **While
you're away** hands your requests to someone until a date.

**My requests** (Chat home): everything you asked an assistant to do that
needed a go-ahead, and where each stands.

- **Invites not yet used** are listed under the form, each with
  **Withdraw**. A link is shown only when it's created: if one is lost,
  invite the person again, and the new link replaces the old. A link
  stops working if whoever sent it is no longer an admin.
- **Signing up with an invite** puts you straight into that organisation.
- **Removing someone** (**Remove**, for admins and owners) shows first
  what they leave behind: their assistants, and who takes them over (you,
  by default); the database connections and MCP servers whose
  credentials they may know, which you should rotate; their pending
  approvals, which close; and the invites they sent, which are withdrawn.
  They lose access straight away and are signed out everywhere. Their
  name stays on what they did. **Reactivate** brings them back, without
  their old assistants.
- **Co-editors.** In an assistant's **History**, under **Who can edit**,
  its creator or an admin adds people who may edit it too. A co-editor
  edits, previews and submits it, but doesn't add others.

### The organisation's settings

**Members › Organisation** shows the organisation's name and three
switches. Admins see them; only the owner changes them, and each change is
in the audit log.

- **Email admins when a version waits for review.**
- **Email builders when their version is decided.**
- **Let the only reviewer approve their own work:** when nobody else can
  review, the owner may approve their own version with a note. Turn it off
  once there is another admin, so every version gets a second pair of
  eyes.

**Eval cases with follow-ups.** A case can ask up to five follow-up
messages in the same conversation (**Then asks**). It is graded on the
last answer, with the tools of every turn counted.

**Builder testing: monthly limit** caps what previews and eval runs
spend each month; Chat's budgets count only real conversations.

**Internal destinations** are the company systems assistants may reach
on your network: a host (or `.corp.example.com` for every name under
it), the address range it must be in, its ports, and whether plain http
is allowed. Everything else stays public-only, and the server's own
machine and networks are never reachable.

**Keeping and erasing** (Members): how many days a conversation is kept
after its last message before it is erased for good (365 by default; admins
change it, and below a year the page warns), legal holds that keep a person
or a conversation out of every purge, and erasing one person's data (both
the owner's), with a list of what was erased.

**How Chat uses your data** (Members, under the settings) is what
everyone reads and accepts before their first message in Chat. The
built-in text says what is kept, that admins can open conversations,
what assistants may do, and the rules; where messages go is added from
the server's setup, with your **Privacy contact**. Only the owner edits
it. Saving with **Ask everyone to accept it again** makes everyone read
the new text before their next message; leave it off for a typo. Every
saved text is under **Earlier texts**.

On most company servers there is one organisation: nobody can create
another, and people join by invite.

### The audit log

Admins and the owner have **Audit log** in the sidebar: who did what,
newest first, from sign-ins to approvals. Narrow it by **What** (for
example **Admins opening conversations**, with the reason each gave),
**Who** and **When**. Nobody can edit or delete an entry.

The owner also has:

- **Export CSV**, the entries shown, as a file. The export is recorded.
- **Check integrity**, which confirms no entry was changed or removed and
  shows the newest fingerprint. Copy it somewhere off the server now and
  then: a later check that no longer reaches it means the record was
  rewritten.

## Privacy, and what each assistant does

The first time you open Chat it shows **How Chat uses your data**: what
is kept, that admins can open your conversations (each time with a reason
that is recorded), the rules, and where your messages go. Read it and
press **I've read it: continue**. If it changes, you'll see it again.

**Privacy & help** (in Chat's bar) has the notice, who to write to about
your data, and **Access to your conversations**: each time an admin opened
one of yours, who it was and why.

**About**, in a conversation, is the assistant's card: who runs it, when
it was approved, what it looks up, what it can do for you (and whether an
admin approves each time), what it remembers about you (with **See what
it remembers** and **Forget all of it**), and what leaves the building.

### Your conversations

Chat lists your conversations with every assistant. The search box finds
one by its title or by anything said in it. **Remove from your history**
takes one out of your lists and search; your company keeps it, and admins
can still open it (each time recorded, and shown on Privacy & help).
**Removed** lists those, and **Put back** returns one.

**The Feedback tab** (builders, on an assistant): thumbs by version, how
many said it solved their problem out of how many answered, and the
latest comments with their question and answer. Never who wrote them.

## Reaching it

Assistant Studio opens on the office network, and from anywhere else with
Tailscale on, phones included. If the page doesn't load:

- outside the office, check that Tailscale is on;
- on an Android phone, Settings › Network › Private DNS should be
  Automatic;
- still nothing: tell your IT team where you are and what the browser
  says.

**On your phone.** Chat works in the phone's browser, and can go on the
home screen like an app: in Safari, Share › **Add to Home Screen**; in
Chrome, the menu › **Install app** (or **Add to home screen**). It opens
on Chat. When it can't reach the server it says so, with your company's
steps for getting on the network. Building assistants still needs a
computer.

**Signing in to the iPhone app with Google** may end up in Safari and
leave the app signed out. Sign in with a code instead: in Safari or on a
computer, open **Your account** › **Make a code**, then type the code into
the app's sign-in page (**Sign in with a code from another device**).

## Your account

**Continue with Google** on the sign-in page, when your company has set
it up, signs you in with your company Google account. Joining from an
invite link, **Join with Google** makes your account with it: it then has
no password, and Google is how you sign in.

The email at the foot of the sidebar opens **Your account**:

- **Two-step sign-in.** A code from an authenticator app after your
  password. Admins and the owner need it for admin features (a banner says
  so). **Set up two-step sign-in**, scan the QR code, type the code, and
  keep the ten recovery codes somewhere safe: each works once if you lose
  your phone. Lost both? Ask whoever runs the server.
- **Connect Google.** If your account has a password, connect your company
  Google account here to sign in with it too. A Google sign-in never takes
  over an account by its address: this is the only way to connect one.
- **Sign in on another device.** **Make a code** shows eight characters
  to type into another device's sign-in page within five minutes. It
  works once. Never give it to anyone, IT included: whoever has it signs
  in as you. With two-step sign-in, the other device still asks for your
  authenticator code.

- **Change your password.** Other devices are signed out; this one stays
  signed in.
- **Confirm your email.** If your address is marked **Not confirmed**,
  **Email me a link** sends one; open it and press **Confirm my email**.
  Joining through an invite confirms it already.

**Forgot your password?** on the sign-in page emails a link to choose a
new one. It works once, for an hour, and signs you out everywhere. If the
server doesn't send email, ask an admin: whoever runs the server can make
you a link.

## Email

When the server sends email, you get: your invite; as an admin, a note
when a version waits for review; as a builder, a note when your version
is approved or rejected. Each only tells you, with a link to the page in
Assistant Studio. **Assistant Studio never asks you to approve anything,
or to type your password, from an email.** If a message asks you to,
don't. Open links on the office network or VPN.

## 11. When it does not do what you expect

| What you see | Look at |
| --- | --- |
| It ignores a document you added | **Sources**: is it **Ready**? Canvas: is the knowledge base connected to the Agent? |
| It says it cannot query the database | Is the database connected on the canvas? Does the database user have permission? |
| It answers from general knowledge, not your documents | **Run details** › was the knowledge base searched? If not, say so in the Agent's instructions |
| An answer is wrong | **Run details** › **Show on canvas**: what it searched, what it found, which tool said what |
| "This conversation has reached its spend limit" | the conversation's cap, or a budget, on **Usage** |
| "Too many conversations are running right now" | the server is busy; try again in a moment |
| Submit for review is greyed out | the **Problems** list |
| "isn't answering yet" in chat | no version is approved yet, or an admin paused it: **History** says which |
| A connection or tool stopped working after a change | **History** › the live version: anything changed since approval is left out until a new version is approved |
| It used to work | **History**: compare the current version with the last good one; then run the eval suite against both |
