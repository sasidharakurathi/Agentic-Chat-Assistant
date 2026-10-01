import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";

import { BrandLockup } from "@/components/brand-mark";
import { DesktopOnlyNote } from "@/components/desktop-only";
import { Countdown } from "@/components/landing/Countdown";
import { Reveal } from "@/components/landing/Reveal";
import { RouteHero } from "@/components/landing/RouteHero";
import { buttonVariants } from "@/components/ui/button";
import { Lamp } from "@/components/ui/lamp";
import { LineBullet } from "@/components/ui/line-bullet";
import { cn } from "@/lib/utils";

/** The landing page (docs/DESIGN.md, Landing). One idea carried through:
 *  an assistant is a route a message travels. The hero plays one message
 *  through it; every section below is calm ruled content on paper, and
 *  shows a real part of the product rather than describing it. */
export default function HomePage() {
  return (
    <div className="min-h-dvh">
      <Header />
      <main>
        <Hero />
        <Steps />
        <Families />
        <Oversight />
        <SelfHost />
        <Closing />
      </main>
      <Footer />
    </div>
  );
}

/** One left edge for the whole page (docs/DESIGN.md section 4). */
function Column({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("mx-auto w-full max-w-[1360px] px-4 sm:px-6 lg:px-10 xl:px-12", className)}>
      {children}
    </div>
  );
}

/** When a group of parts starts coming in (--t), and a part's offset
 *  within its group (--dt), for the .rv classes in globals.css. */
const t = (s: number) => ({ "--t": `${s}s` }) as CSSProperties;
const dt = (s: number) => ({ "--dt": `${s}s` }) as CSSProperties;

/** A section: a rule above it, its heading and one sentence, then its body. */
function Section({
  id,
  title,
  lead,
  children,
}: {
  id: string;
  title: string;
  lead: string;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="border-border border-t py-16 sm:py-20">
      <Column>
        <Reveal>
          {/* Wide screens: the heading on the left, its sentence on the
              right under its last line, as in the hero. */}
          <div className="grid gap-x-16 gap-y-3 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)] lg:items-end">
            <h2
              id={id}
              className="rv font-condensed text-h1 sm:text-display max-w-[24ch] font-semibold"
            >
              {title}
            </h2>
            <p className="rv rv-d1 text-muted-foreground text-reading max-w-[60ch]">{lead}</p>
          </div>
          <div className="mt-10 lg:mt-12" style={t(0.25)}>
            {children}
          </div>
        </Reveal>
      </Column>
    </section>
  );
}

function Header() {
  return (
    <header>
      <Column className="flex h-16 items-center justify-between gap-4">
        <Link
          href="/"
          aria-label="Assistant Studio home"
          className="focus-visible:ring-ring rounded-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
        >
          <BrandLockup />
        </Link>
        {/* Signing in is for a computer: no account links on a phone. */}
        <nav aria-label="Account" className="hidden items-center gap-2 md:flex">
          <Link href="/login" className={buttonVariants({ variant: "ghost", size: "sm" })}>
            Sign in
          </Link>
          <Link href="/register" className={buttonVariants({ size: "sm" })}>
            Create an account
          </Link>
        </nav>
      </Column>
    </header>
  );
}

function Hero() {
  return (
    <section aria-labelledby="hero-title" className="pt-12 pb-16 sm:pt-20 sm:pb-24">
      <Column>
        {/* Wide screens: the headline on the left, the sentence and the
            actions under its last line on the right. */}
        <div className="grid gap-x-16 gap-y-6 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)] lg:items-end">
          <h1
            id="hero-title"
            className="load-rise font-condensed text-display sm:text-display-xl max-w-[18ch] font-semibold"
          >
            Build chat assistants on your own documents, databases and tools.
          </h1>
          <div className="load-rise lg:pb-1.5" style={dt(0.15)}>
            <p className="text-muted-foreground max-w-[58ch] text-[17px] leading-[27px]">
              Connect what an assistant may use on a canvas, chat with it, and see every step it
              took: what it searched, what it ran and what it cost. It asks a person before it
              changes anything.
            </p>
            <DesktopOnlyNote className="mt-7 md:hidden" />
            <div className="mt-7 hidden flex-wrap gap-3 md:flex">
              <Link href="/register" className={buttonVariants({ size: "lg" })}>
                Create an account
              </Link>
              <Link href="/login" className={buttonVariants({ variant: "outline", size: "lg" })}>
                Sign in
              </Link>
            </div>
          </div>
        </div>
        <div className="mt-14 sm:mt-16">
          <RouteHero />
        </div>
      </Column>
    </section>
  );
}

/* ── From a blank canvas to a published assistant ─────────────────── */

const STEPS = [
  {
    name: "Draw it",
    text: "Add what the assistant may use and connect it to the Agent: documents, a database, tools. Problems show on the canvas, most with a fix one click away.",
    detail: "Or start from a sample: a support desk, a team notebook, a research desk.",
  },
  {
    name: "Try it",
    text: "Chat with the draft. Every answer shows the steps it took, how long each took, and what it cost, and can be shown on the canvas.",
    detail: "Answers keep going if you reload or switch conversations.",
  },
  {
    name: "Measure it",
    text: "Write test questions with what a good answer contains and which document it should find. Run them against the draft and against what is live.",
    detail: "Each run compares with the last, case by case.",
  },
  {
    name: "Publish it",
    text: "People chat with the published version while you keep working on the draft. Versions never change, and any two compare as one drawing.",
    detail: "Every version keeps a note of what changed, and who published it.",
  },
] as const;

function Steps() {
  return (
    <Section
      id="steps-title"
      title="From a blank canvas to a published assistant"
      lead="Four steps, in this order. The same route a message takes is the route you build."
    >
      <ol className="relative grid gap-x-10 gap-y-10 md:grid-cols-4 md:gap-y-0">
        {/* The main line behind the stops: horizontal on wide screens,
            vertical on narrow ones. */}
        <span
          aria-hidden
          className="rv-x bg-node-main absolute top-[13px] right-0 left-[13px] hidden h-[5px] md:block"
        />
        <span
          aria-hidden
          className="rv-y bg-node-main absolute top-[13px] bottom-0 left-[11.5px] w-[5px] md:hidden"
        />
        {STEPS.map((step, i) => (
          <li key={step.name} className="relative pl-11 md:pl-0" style={t(0.3 + i * 0.28)}>
            <LineBullet
              type="input"
              number={i + 1}
              size={24}
              decorative
              className="rv-pop ring-background absolute top-0.5 left-0 ring-4 md:static"
            />
            <h3 className="rv rv-d1 font-condensed mt-0 text-[20px] leading-[26px] font-semibold md:mt-5">
              {step.name}
            </h3>
            <p className="rv rv-d2 text-reading mt-2 max-w-[44ch]">{step.text}</p>
            <p className="rv rv-d3 text-muted-foreground mt-3 max-w-[44ch] text-sm">
              {step.detail}
            </p>
          </li>
        ))}
      </ol>
    </Section>
  );
}

/* ── What an assistant can use: the Key ───────────────────────────── */

const FAMILIES = [
  {
    name: "Knowledge",
    stroke: "bg-node-kb",
    blurb: "What it can read.",
    items: [
      {
        type: "knowledge_base",
        name: "Knowledge base",
        text: "Searches your files, web pages and pasted text by meaning and by words, then cites what it used.",
      },
      {
        type: "data_source",
        name: "Data source",
        text: "One document: a PDF, a Word file, a web page, or text you paste in.",
      },
      {
        type: "memory",
        name: "Memory",
        text: "Long conversations summarised, and notes kept between conversations if you allow it.",
      },
    ],
  },
  {
    name: "Actions",
    stroke: "bg-node-action",
    blurb: "What it can do.",
    items: [
      {
        type: "database",
        name: "Database",
        text: "Postgres, MySQL, SQLite or MongoDB. Read-only unless you allow writes, and every write asks first.",
      },
      {
        type: "tool",
        name: "Tool",
        text: "Web search, HTTP requests to sites you list, a calculator, and the date and time.",
      },
      {
        type: "mcp_server",
        name: "MCP server",
        text: "Tools from other systems, such as a ticket tracker or a code host, each allowed one by one.",
      },
    ],
  },
  {
    name: "Helpers",
    stroke: "bg-node-subagent",
    blurb: "Who it can hand work to.",
    items: [
      {
        type: "subagent",
        name: "Subagent",
        text: "A helper with a narrower job (searching, writing SQL, researching) and a model of its own.",
      },
      {
        type: "router",
        name: "Router",
        text: "Decides how much effort each message deserves, so a greeting is not treated like an analysis.",
      },
    ],
  },
] as const;

function Families() {
  return (
    <Section
      id="families-title"
      title="What an assistant can use"
      lead="Everything joins the Agent on a line of its own colour, so a pipeline reads at a glance: knowledge, actions, helpers."
    >
      <div className="grid gap-12 md:grid-cols-3 md:gap-10">
        {FAMILIES.map((f, i) => (
          <div key={f.name} style={t(0.25 + i * 0.15)}>
            <div className="flex items-center gap-3">
              <span aria-hidden className={cn("rv-x h-[5px] w-10 shrink-0", f.stroke)} />
              <h3 className="rv rv-d1 font-condensed text-[20px] leading-[26px] font-semibold">
                {f.name}
              </h3>
            </div>
            <p className="rv rv-d1 text-muted-foreground mt-1 text-sm">{f.blurb}</p>
            <ul className="border-border mt-5 border-t">
              {f.items.map((item, j) => (
                <li
                  key={item.name}
                  className="rv border-border flex gap-3 border-b py-4"
                  style={dt(0.3 + j * 0.1)}
                >
                  <LineBullet type={item.type} size={24} decorative className="mt-px" />
                  <div className="min-w-0">
                    <p className="font-condensed text-[15px] leading-5 font-semibold">
                      {item.name}
                    </p>
                    <p className="text-muted-foreground mt-1 text-sm">{item.text}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Section>
  );
}

/* ── Oversight: approvals and the route of a run ─────────────────── */

function Oversight() {
  return (
    <Section
      id="oversight-title"
      title="Nothing changes without a person, and every step shows"
      lead="Reading is automatic. Changing things is not: the assistant stops and shows exactly what it is about to run."
    >
      {/* Wide: the two stills side by side at one height, their notes
          under them. Narrow: each still with its own notes. */}
      <div className="grid gap-x-16 gap-y-8 lg:grid-cols-2">
        <ApprovalExample />
        <ul className="flex flex-col gap-4 lg:order-3">
          <Point at={0} tone="warning">
            A database write, an HTTP request or a tool you mark waits for a decision. No answer in
            five minutes means it does not run.
          </Point>
          <Point at={1} tone="success">
            Only the person in the conversation, or an admin, can approve. They see the statement
            itself, never a summary of it.
          </Point>
          <Point at={2} tone="destructive">
            Budgets per conversation, assistant and organization stop chats at 100%, and warn from
            80%.
          </Point>
        </ul>
        <RouteExample />
        <ul className="flex flex-col gap-4 lg:order-4">
          <Point at={0} tone="success">
            Each step with its time, and the whole answer with its tokens and cost.
          </Point>
          <Point at={1} tone="success">
            Show on canvas lights up the part of the pipeline that answer went through.
          </Point>
          <Point at={2} tone="warning">
            Instructions hidden in documents and web pages are flagged before the model acts on
            them.
          </Point>
        </ul>
      </div>
    </Section>
  );
}

function Point({
  at,
  tone,
  children,
}: {
  at: number;
  tone: "success" | "warning" | "destructive";
  children: ReactNode;
}) {
  return (
    <li className="rv flex gap-3 text-sm" style={dt(0.45 + at * 0.1)}>
      <Lamp tone={tone} className="mt-[6px]" />
      <span className="max-w-[56ch]">{children}</span>
    </li>
  );
}

/** A still of the approval card as the chat shows it. Not interactive:
 *  its buttons are drawn, not buttons. */
function ApprovalExample() {
  return (
    <figure
      className="rv bg-card border-border flex flex-col rounded-lg border p-5 lg:order-1"
      aria-label="An approval card, as it appears in a chat"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-2 text-sm font-semibold">
          <Lamp tone="warning" live />
          Approval needed
        </span>
        <Countdown />
      </div>
      <p className="text-muted-foreground mt-3 text-sm">Orders DB wants to run this statement:</p>
      <pre className="bg-muted text-code mt-2 overflow-x-auto rounded-sm px-3 py-2.5 font-mono">
        {"UPDATE orders\nSET status = 'refunded'\nWHERE id = 4417;"}
      </pre>
      <div aria-hidden className="mt-4 flex gap-2">
        <span className={cn(buttonVariants({ size: "sm" }), "pointer-events-none")}>Deny</span>
        <span
          className={cn(buttonVariants({ variant: "outline", size: "sm" }), "pointer-events-none")}
        >
          Approve
        </span>
      </div>
      <figcaption className="text-small text-muted-foreground mt-auto pt-4">
        Deny is the default. Nothing runs until someone decides.
      </figcaption>
    </figure>
  );
}

const ROUTE = [
  {
    type: "input",
    n: 1,
    name: "Input",
    detail: "Can I return the lamp from order 4417?",
    time: "",
  },
  { type: "guardrail", n: 2, name: "Guardrails", detail: "Passed", time: "9 ms" },
  {
    type: "knowledge_base",
    name: "Knowledge base",
    detail: "4 passages from Refund policy.pdf",
    time: "412 ms",
  },
  { type: "database", name: "Orders DB", detail: "1 row, approved by Priya", time: "1.1 s" },
  { type: "agent", n: 4, name: "Agent", detail: "Answered, with 1 citation", time: "2.4 s" },
] as const;

/** A still of a run's route, the strip Run details draws: the main line
 *  with the steps that joined it, in their own colours. */
function RouteExample() {
  return (
    <figure
      className="rv rv-d1 bg-card border-border rounded-lg border p-5 lg:order-2"
      aria-label="The route of one answer, as Run details shows it"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-semibold">Run details</span>
        <span className="text-small text-muted-foreground">Sonnet 5</span>
      </div>
      <ol className="relative mt-4">
        <span
          aria-hidden
          className="rv-y bg-node-main absolute top-3 bottom-3 left-[9.5px] w-[3px]"
          style={dt(0.3)}
        />
        {ROUTE.map((step, k) => (
          <li
            key={step.name}
            className="rv relative flex items-start gap-3 py-2"
            style={dt(0.35 + k * 0.2)}
          >
            <LineBullet
              type={step.type}
              number={"n" in step ? step.n : undefined}
              size={20}
              decorative
              className="ring-card ring-4"
            />
            <div className="min-w-0 flex-1">
              <p className="font-condensed text-[15px] leading-5 font-semibold">{step.name}</p>
              <p className="text-muted-foreground truncate text-sm">{step.detail}</p>
            </div>
            <span className="text-small text-muted-foreground num pt-0.5">{step.time}</span>
          </li>
        ))}
      </ol>
      <dl className="border-border mt-3 grid grid-cols-3 gap-3 border-t pt-4">
        <Figure label="Cost" value="$0.0041" />
        <Figure label="Tokens" value="1,284" />
        <Figure label="Time" value="2.4 s" />
      </dl>
    </figure>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-small text-muted-foreground">{label}</dt>
      <dd className="num mt-0.5 text-sm font-semibold">{value}</dd>
    </div>
  );
}

/* ── Self-hosting ─────────────────────────────────────────────────── */

const FACTS = [
  ["Your data", "Postgres with pgvector for everything stored, MinIO for files. On your machine."],
  ["The model", "Claude, on your own Anthropic API key. Budgets keep the spend where you set it."],
  ["Search", "Voyage embeddings and reranking, or a local model that needs no key and no network."],
  ["Sign-up", "Open, or by invitation only: the first account, then whoever you invite."],
  [
    "Backups",
    "Scripts that back up, prove a backup restores into a scratch database, and restore.",
  ],
  ["Watching it", "Health checks, Prometheus metrics, a Grafana dashboard and alert rules."],
] as const;

/** What the preflight prints for a good env file with one warning. */
const PREFLIGHT = [
  ["ok", "settings: APP_ENV=production"],
  ["ok", "database"],
  ["ok", "encryption key: opens the stored credentials"],
  ["WARN", "METRICS_TOKEN: not set: /metrics is switched off"],
  ["", "ready to start"],
] as const;

function SelfHost() {
  return (
    <Section
      id="selfhost-title"
      title="Runs on your own machine"
      lead="One Docker Compose file: TLS at the front, the databases off the internet, and a check on start that says in plain words what is missing."
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-16">
        <div>
          <pre className="rv bg-card border-border text-code overflow-x-auto rounded-lg border p-5 font-mono">
            <code>
              {"cp deploy/production.env.example .env.production\n"}
              {"docker compose --env-file .env.production \\\n"}
              {"  -f docker-compose.prod.yml up -d --build"}
            </code>
          </pre>
          <p className="rv rv-d1 text-muted-foreground mt-4 max-w-[56ch] text-sm">
            Fill in the env file first. Before anything starts, a check reads it and says what is
            wrong, without printing a secret:
          </p>
          <pre className="rv rv-d2 bg-card border-border text-code mt-3 overflow-x-auto rounded-lg border p-5 font-mono">
            <code>
              {PREFLIGHT.map(([status, words], k) => (
                <span key={words} className="rv block" style={dt(0.6 + k * 0.25)}>
                  <span className="text-muted-foreground">[preflight]</span>{" "}
                  {status ? (
                    <>
                      <span className={status === "ok" ? "text-success" : "text-warning"}>
                        {status.padEnd(5)}
                      </span>{" "}
                    </>
                  ) : null}
                  {words}
                </span>
              ))}
            </code>
          </pre>
        </div>
        <dl className="border-border border-t">
          {FACTS.map(([label, value], k) => (
            <div
              key={label}
              className="rv border-border grid gap-1 border-b py-3.5 sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-4"
              style={dt(0.15 + k * 0.08)}
            >
              <dt className="font-condensed text-[15px] leading-5 font-semibold">{label}</dt>
              <dd className="text-muted-foreground text-sm">{value}</dd>
            </div>
          ))}
        </dl>
      </div>
    </Section>
  );
}

/* ── The end of the line ──────────────────────────────────────────── */

function Closing() {
  return (
    <section aria-labelledby="closing-title" className="border-border border-t py-16 sm:py-24">
      <Column>
        <Reveal>
          <div className="flex flex-col gap-8 md:flex-row md:items-end md:justify-between">
            <div>
              <h2
                id="closing-title"
                className="rv font-condensed text-h1 sm:text-display font-semibold"
              >
                Start from a sample
              </h2>
              <p className="rv rv-d1 text-muted-foreground text-reading mt-3 max-w-[56ch]">
                Three finished assistants, each with its own test questions: a store support desk, a
                team notebook and a research desk. Open one on the canvas and change anything.
              </p>
            </div>
            <DesktopOnlyNote className="rv rv-d2 md:hidden" />
            <div className="rv rv-d2 hidden flex-wrap gap-3 md:flex">
              <Link href="/register" className={buttonVariants({ size: "lg" })}>
                Create an account
              </Link>
              <Link href="/login" className={buttonVariants({ variant: "outline", size: "lg" })}>
                Sign in
              </Link>
            </div>
          </div>
          {/* The main line runs out to a terminus. */}
          <div aria-hidden className="mt-14 flex items-center" style={t(0.3)}>
            <span className="rv-x bg-node-main h-[5px] flex-1" />
            <span
              className="rv-pop border-node-main bg-background size-4 rounded-full border-[5px]"
              style={dt(1.1)}
            />
          </div>
        </Reveal>
      </Column>
    </section>
  );
}

function Footer() {
  return (
    <footer>
      <Column className="text-small text-muted-foreground flex flex-wrap items-center justify-between gap-4 pb-10">
        <BrandLockup />
        <span className="num">Version 1.0.0. Self-hosted.</span>
      </Column>
    </footer>
  );
}
