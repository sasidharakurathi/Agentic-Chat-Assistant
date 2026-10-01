"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";

import { cn } from "@/lib/utils";

/** When each part of the drawing comes in (seconds from load). After it,
 *  a message travels the line on a loop: the `route-loop` keyframes in
 *  globals.css, one 9 s cycle shared by every moving part. */
const T = { line: 0.1, branches: 0.9 } as const;

const at = (d: number, dur?: number): CSSProperties =>
  ({ "--d": `${d}s`, ...(dur ? { "--dur": `${dur}s` } : {}) }) as CSSProperties;

/** Read by screen readers. An aria-label rather than an SVG <title>, which
 *  browsers show as a tooltip on hover. */
const LABEL =
  "A support assistant answering one message. A message comes in at step 1, Input, passes " +
  "Guardrails and the Router, and reaches the Agent. The Agent searches a knowledge base " +
  "built from Refund policy.pdf, then queries the Orders DB database, and the answer leaves " +
  "at step 5, Output, citing the refund policy.";

const STATUSES = [
  ["route-s-wait", "Waiting for a message"],
  ["route-s-kb", "Searching the knowledge base"],
  ["route-s-db", "Querying Orders DB"],
  ["route-s-done", "Answered in 2.4 s"],
] as const;

/** The hero (docs/DESIGN.md, Landing): one assistant's pipeline, drawn the
 *  way the builder draws it, with a message travelling it on a loop. Every
 *  word in it is the product's own. Wide screens get the line across; a
 *  phone gets it down the screen, at a size it can read. Reduced motion
 *  shows the answered state, still. */
export function RouteHero() {
  // Off screen, the loop stops: nothing paints that nobody sees.
  const ref = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(([entry]) => setVisible(entry?.isIntersecting ?? true));
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <figure
      ref={ref}
      className={cn(
        "landing-panel relative overflow-hidden rounded-lg border",
        !visible && "route-paused",
      )}
    >
      <WideRoute />
      <TallRoute />
      <figcaption className="border-border text-small text-muted-foreground border-t px-4 py-2.5">
        One message through a support assistant, as the builder draws it.
      </figcaption>
    </figure>
  );
}

/* ── Wide screens: the main line across ──────────────────────────── */

/** The Agent's plate. Wide enough for its longest status line. */
const PLATE = { x: 552, y: 172, w: 236, h: 56 } as const;

function WideRoute() {
  return (
    <svg
      viewBox="0 0 1040 380"
      role="img"
      aria-label={LABEL}
      className="landing-route hidden h-auto w-full md:block"
    >
      {/* ── lines ─────────────────────────────────────────── */}
      <g fill="none" strokeLinecap="butt">
        {/* Knowledge: a document feeds the knowledge base, which joins the
            Agent from above. */}
        <Line d="M250 72H420" colour="stroke-node-datasource" at={at(T.branches, 0.4)} />
        <Line
          d="M420 72H630Q650 72 650 92V172"
          colour="stroke-node-kb"
          at={at(T.branches + 0.2, 0.6)}
        />
        {/* Actions: a database and a tool join from below. */}
        <Line
          d="M300 334H630Q650 334 650 314V228"
          colour="stroke-node-action"
          at={at(T.branches + 0.3, 0.7)}
        />
        <Line
          d="M440 284H590Q610 284 610 264V228"
          colour="stroke-node-action"
          at={at(T.branches + 0.45, 0.6)}
        />
        {/* The main line, through the Agent to Output. */}
        <Line d="M70 200H900" colour="stroke-node-main" width={5} at={at(T.line, 1.1)} />
        {/* "In use": a marker dash runs down a line into the Agent. */}
        <Pulse d="M420 72H630Q650 72 650 92V172" which="kb" />
        <Pulse d="M300 334H630Q650 334 650 314V228" which="db" />
      </g>

      {/* The message: drawn before the stops and the plate so it passes
          behind them. It starts hidden under Input, goes behind the Agent
          while it works, and ends hidden under Output. */}
      <Token cx={70} cy={200} />

      {/* ── branches ──────────────────────────────────────── */}
      <g className="font-condensed" fontWeight={600} fontSize={15}>
        <Branch
          x={250}
          y={72}
          colour="fill-node-datasource"
          name="Refund policy.pdf"
          detail="Data source"
          d={T.branches + 0.1}
          place="above"
        />
        <Branch
          x={420}
          y={72}
          colour="fill-node-kb"
          name="Knowledge base"
          detail="4 documents"
          d={T.branches + 0.3}
          place="above"
        />
        <Branch
          x={300}
          y={334}
          colour="fill-node-action"
          name="Orders DB"
          detail="Database, read-only"
          d={T.branches + 0.4}
          place="left"
        />
        <Branch
          x={440}
          y={284}
          colour="fill-node-action"
          name="Calculator"
          detail="Tool"
          d={T.branches + 0.55}
          place="left"
        />
      </g>

      {/* ── stops ─────────────────────────────────────────── */}
      <g className="font-condensed" fontWeight={600}>
        {(
          [
            [1, 70, "Input"],
            [2, 240, "Guardrails"],
            [3, 410, "Router"],
          ] as const
        ).map(([n, x, name], i) => (
          <Stop key={n} n={n} cx={x} cy={200} name={name} d={T.line + 0.12 + i * 0.22} below />
        ))}
        <Plate {...PLATE} d={T.line + 0.78} />
        <Stop n={5} cx={900} cy={200} name="Output" d={T.line + 1.0} below />
      </g>

      <Answer x={770} y={262} />
    </svg>
  );
}

/* ── Phones: the main line down the screen ───────────────────────── */

/** The plate on a phone: full width, the main line through its left end. */
const TALL_PLATE = { x: 30, y: 330, w: 300, h: 60 } as const;

function TallRoute() {
  return (
    <svg
      viewBox="0 0 360 730"
      role="img"
      aria-label={LABEL}
      className="landing-route landing-route-tall block h-auto w-full md:hidden"
    >
      <g fill="none" strokeLinecap="butt">
        {/* Knowledge comes down the right into the Agent from above. */}
        <Line d="M300 110V210" colour="stroke-node-datasource" at={at(T.branches, 0.4)} />
        <Line d="M300 210V330" colour="stroke-node-kb" at={at(T.branches + 0.2, 0.5)} />
        {/* Actions come up into it from below. */}
        <Line d="M300 520V390" colour="stroke-node-action" at={at(T.branches + 0.3, 0.6)} />
        <Line d="M260 450V390" colour="stroke-node-action" at={at(T.branches + 0.45, 0.4)} />
        <Line d="M60 60V590" colour="stroke-node-main" width={5} at={at(T.line, 1.1)} />
        <Pulse d="M300 210V330" which="kb" />
        <Pulse d="M300 520V390" which="db" />
      </g>

      <Token cx={60} cy={60} />

      <g className="font-condensed" fontWeight={600} fontSize={15}>
        <Branch
          x={300}
          y={110}
          colour="fill-node-datasource"
          name="Refund policy.pdf"
          detail="Data source"
          d={T.branches + 0.1}
          place="left"
        />
        <Branch
          x={300}
          y={210}
          colour="fill-node-kb"
          name="Knowledge base"
          detail="4 documents"
          d={T.branches + 0.3}
          place="left"
        />
        <Branch
          x={260}
          y={450}
          colour="fill-node-action"
          name="Calculator"
          detail="Tool"
          d={T.branches + 0.55}
          place="left"
        />
        <Branch
          x={300}
          y={520}
          colour="fill-node-action"
          name="Orders DB"
          detail="Database, read-only"
          d={T.branches + 0.4}
          place="left"
        />
      </g>

      <g className="font-condensed" fontWeight={600}>
        {(
          [
            [1, 60, "Input"],
            [2, 160, "Guardrails"],
            [3, 260, "Router"],
          ] as const
        ).map(([n, y, name], i) => (
          <Stop key={n} n={n} cx={60} cy={y} name={name} d={T.line + 0.12 + i * 0.22} />
        ))}
        <Plate {...TALL_PLATE} d={T.line + 0.78} />
        <Stop n={5} cx={60} cy={590} name="Output" d={T.line + 1.0} />
      </g>

      <Answer x={30} y={624} />
    </svg>
  );
}

/* ── parts ───────────────────────────────────────────────────────── */

function Line({
  d,
  colour,
  width = 3,
  at: style,
}: {
  d: string;
  colour: string;
  width?: number;
  at: CSSProperties;
}) {
  return (
    <path
      d={d}
      pathLength={1}
      strokeWidth={width}
      className={cn("route-draw", colour)}
      style={style}
    />
  );
}

function Pulse({ d, which }: { d: string; which: "kb" | "db" }) {
  return (
    <path
      d={d}
      pathLength={1}
      strokeWidth={5}
      className={cn(
        "route-loop stroke-marker",
        which === "kb" ? "route-pulse-kb" : "route-pulse-db",
      )}
    />
  );
}

function Token({ cx, cy }: { cx: number; cy: number }) {
  return (
    <g className="route-pop" style={at(T.line + 0.12)}>
      <circle
        cx={cx}
        cy={cy}
        r={8}
        strokeWidth={2.5}
        className="route-loop route-token fill-marker stroke-node-main"
      />
    </g>
  );
}

/** A main-line stop: its number in the bullet, its name below it (wide)
 *  or beside it (tall). */
function Stop({
  n,
  cx,
  cy,
  name,
  d,
  below = false,
}: {
  n: number;
  cx: number;
  cy: number;
  name: string;
  d: number;
  below?: boolean;
}) {
  return (
    <g className="route-pop" style={at(d)}>
      <circle cx={cx} cy={cy} r={14} className="fill-node-main" />
      <text x={cx} y={cy + 5} textAnchor="middle" fontSize={14} className="fill-node-icon num">
        {n}
      </text>
      <text
        x={below ? cx : cx + 28}
        y={below ? cy + 38 : cy + 5}
        textAnchor={below ? "middle" : "start"}
        fontSize={15}
        className="fill-foreground"
      >
        {name}
      </text>
    </g>
  );
}

/** The Agent: a plate with an ink edge that lights while it works, and a
 *  line of status that says what it is doing. The main line runs through
 *  its number. */
function Plate({ x, y, w, h, d }: { x: number; y: number; w: number; h: number; d: number }) {
  const cx = x + 30;
  const cy = y + h / 2;
  return (
    <g className="route-pop" style={at(d)}>
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={6}
        strokeWidth={2.5}
        className="fill-card stroke-node-main"
      />
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={6}
        strokeWidth={2.5}
        className="route-loop route-busy stroke-marker fill-none"
      />
      <circle cx={cx} cy={cy} r={13} className="fill-node-main" />
      <text x={cx} y={cy + 5} textAnchor="middle" fontSize={13} className="fill-node-icon">
        4
      </text>
      <text x={cx + 22} y={cy - 4} fontSize={15} className="fill-foreground">
        Agent
      </text>
      <g className="font-sans" fontWeight={400} fontSize={12}>
        {STATUSES.map(([cls, words]) => (
          <text
            key={cls}
            x={cx + 22}
            y={cy + 14}
            className={cn("route-loop fill-muted-foreground", cls)}
          >
            {words}
          </text>
        ))}
      </g>
    </g>
  );
}

/** A branch bullet with its name and detail, above the bullet or to its
 *  left, clear of the bullet and of every line. */
function Branch({
  x,
  y,
  colour,
  name,
  detail,
  d,
  place,
}: {
  x: number;
  y: number;
  colour: string;
  name: string;
  detail: string;
  d: number;
  place: "above" | "left";
}) {
  const left = place === "left";
  const nameX = left ? x - 18 : x;
  const nameY = left ? y - 3 : y - 38;
  const anchor = left ? "end" : "middle";
  return (
    <g className="route-pop" style={at(d)}>
      <circle cx={x} cy={y} r={10} className={colour} />
      <text x={nameX} y={nameY} textAnchor={anchor} className="fill-foreground">
        {name}
      </text>
      <text
        x={nameX}
        y={nameY + 17}
        textAnchor={anchor}
        fontSize={12}
        fontWeight={400}
        className="fill-muted-foreground font-sans"
      >
        {detail}
      </text>
    </g>
  );
}

/** The answer as the chat shows it: the words, a citation, the source and
 *  the cost. 252 wide, 92 high. */
function Answer({ x, y }: { x: number; y: number }) {
  return (
    <g className="route-loop route-answer">
      <rect
        x={x}
        y={y}
        width={252}
        height={92}
        rx={8}
        strokeWidth={1}
        className="fill-card stroke-border"
      />
      <text x={x + 16} y={y + 26} fontSize={13} className="fill-foreground">
        Refunds are accepted within 30 days
      </text>
      <text x={x + 16} y={y + 44} fontSize={13} className="fill-foreground">
        of delivery, for unused items.
        <tspan dx={4} fontSize={11} fontWeight={600} className="fill-node-kb">
          [1]
        </tspan>
      </text>
      <line
        x1={x + 16}
        x2={x + 236}
        y1={y + 58}
        y2={y + 58}
        strokeWidth={1}
        className="stroke-border"
      />
      <circle cx={x + 22} cy={y + 75} r={4} className="fill-node-kb" />
      <text x={x + 32} y={y + 79} fontSize={12} className="fill-muted-foreground">
        Refund policy.pdf
      </text>
      <text
        x={x + 236}
        y={y + 79}
        fontSize={12}
        textAnchor="end"
        className="fill-muted-foreground num"
      >
        $0.004
      </text>
    </g>
  );
}
