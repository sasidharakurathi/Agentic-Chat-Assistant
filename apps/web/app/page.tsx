import Link from "next/link";

import { BrandMark } from "@/components/brand-mark";
import { buttonVariants } from "@/components/ui/button";

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-3xl flex-col px-4 pt-[18vh] pb-16 sm:px-8 sm:pt-[28vh]">
      <h1 className="font-condensed text-h1 sm:text-display flex items-center gap-3 font-semibold">
        <BrandMark className="h-[22px] w-10 sm:h-[27px] sm:w-12" />
        Assistant Studio
      </h1>
      <p className="text-muted-foreground text-reading mt-4 max-w-[60ch]">
        Build an assistant by wiring together what it can use: your documents, databases, tools and
        MCP servers. Then chat with it and see every step it took.
      </p>
      <div className="mt-8 flex flex-wrap gap-3">
        <Link href="/register" className={buttonVariants({ size: "lg" })}>
          Create an account
        </Link>
        <Link href="/login" className={buttonVariants({ variant: "outline", size: "lg" })}>
          Sign in
        </Link>
      </div>
      <MainLine className="mt-16 w-full max-w-[600px]" />
    </main>
  );
}

/** The only illustration: a pipeline drawn as a line diagram. Four numbered
 *  stops on the main line, a knowledge line joining the Agent from above
 *  and an action line from below. Static, and described in words. */
function MainLine({ className }: { className?: string }) {
  const stops = [
    { x: 60, n: 1, name: "Input" },
    { x: 190, n: 2, name: "Guardrails" },
    { x: 360, n: 3, name: "Agent" },
    { x: 540, n: 4, name: "Output" },
  ];
  return (
    <svg
      viewBox="0 0 600 224"
      role="img"
      aria-label="A pipeline in four steps: Input, Guardrails, Agent, Output. A knowledge base joins the Agent from above and a database from below."
      className={className}
    >
      <g fill="none" strokeLinecap="butt">
        <path d="M250 36H340Q360 36 360 56V96" className="stroke-node-kb" strokeWidth={3} />
        <path d="M250 188H340Q360 188 360 168V124" className="stroke-node-action" strokeWidth={3} />
        <path d="M60 110H540" className="stroke-node-main" strokeWidth={5} />
      </g>

      <g className="font-condensed" fontWeight={600} fontSize={14}>
        <circle cx={250} cy={36} r={9} className="fill-node-kb" />
        <text x={234} y={41} textAnchor="end" className="fill-foreground">
          Knowledge base
        </text>
        <circle cx={250} cy={188} r={9} className="fill-node-action" />
        <text x={234} y={193} textAnchor="end" className="fill-foreground">
          Database
        </text>

        {stops.map((s) => (
          <g key={s.n}>
            <circle cx={s.x} cy={110} r={13} className="fill-node-main" />
            <text x={s.x} y={115} textAnchor="middle" fontSize={13} className="fill-node-icon">
              {s.n}
            </text>
            <text
              x={s.name === "Agent" ? s.x + 16 : s.x}
              y={146}
              textAnchor={s.name === "Agent" ? "start" : "middle"}
              className="fill-foreground"
            >
              {s.name}
            </text>
          </g>
        ))}
      </g>
    </svg>
  );
}
