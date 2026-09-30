"use client";

import { useState, type ReactNode } from "react";

import { prefersReducedMotion } from "@/components/canvas/canvas-view";
import { mainLineStops } from "@/components/canvas/graph-sync";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Lamp } from "@/components/ui/lamp";
import { LineBullet } from "@/components/ui/line-bullet";
import { SectionHeading } from "@/components/ui/section-heading";
import { Select } from "@/components/ui/select";
import type { Graph, ValidationResult } from "@/lib/api";
import { cn } from "@/lib/utils";

export type SettingsSection = {
  id: string;
  title: string;
  description: string;
  /** A main-line stop: numbered like its station on the canvas. */
  stopType?: string;
  /** One of the Agent's own settings: indented in "On this page". */
  sub?: boolean;
  /** Node types whose problems belong to this section. */
  types: string[];
  /** Null for a stop with nothing to set: just its heading and description. */
  body: ReactNode;
};

/** The builder's Settings tab (docs/DESIGN.md section 7): one column of
 *  sections, each an H2 and a line of description separated by rules, with
 *  an "On this page" list beside it from 1024px up and a "Jump to" select
 *  below that. The list numbers only the pipeline stops, like the canvas,
 *  indents the Agent's own settings, and puts a lamp by sections with
 *  problems. Choosing an entry scrolls to its section and moves focus to
 *  its heading. */
export function SettingsView({
  graph,
  validation,
  sections: maybe,
  problems,
}: {
  graph: Graph;
  validation: ValidationResult;
  /** Falsy entries are skipped, so a caller can leave out what it lacks. */
  sections: (SettingsSection | null | false | undefined)[];
  /** The problems list, shown above the sections when there are any. */
  problems: ReactNode;
}) {
  const [jump, setJump] = useState("");
  const sections = maybe.filter((s): s is SettingsSection => Boolean(s));
  const stops = mainLineStops(graph);
  const stopOf = (type?: string) => {
    const n = type ? graph.nodes.find((x) => x.type === type) : undefined;
    return n ? stops.get(n.id) : undefined;
  };
  const typeOf = new Map(graph.nodes.map((n) => [n.id, n.type]));
  const tone = (s: SettingsSection): "destructive" | "warning" | null => {
    const hit = (list: ValidationResult["errors"]) =>
      list.some((i) => i.node_id && s.types.includes(typeOf.get(i.node_id) ?? ""));
    return hit(validation.errors) ? "destructive" : hit(validation.warnings) ? "warning" : null;
  };
  const headingId = (sectionId: string) => `settings-${sectionId}`;
  const jumpTo = (sectionId: string) => {
    const el = document.getElementById(headingId(sectionId));
    if (!el) return;
    el.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
    el.focus({ preventScroll: true });
  };
  const hasProblems = validation.errors.length > 0 || validation.warnings.length > 0;

  return (
    <div className="mx-auto w-full max-w-[944px] px-4 py-6 md:px-8 lg:grid lg:grid-cols-[208px_minmax(0,640px)] lg:gap-12">
      <nav aria-label="On this page" className="hidden lg:sticky lg:top-6 lg:block lg:self-start">
        <p className="text-h4 mb-2 font-semibold">On this page</p>
        <ol className="flex flex-col gap-0.5">
          {sections.map((s) => {
            const stop = stopOf(s.stopType);
            const t = tone(s);
            return (
              <li key={s.id} className={cn(s.sub && "pl-6")}>
                <a
                  href={`#${headingId(s.id)}`}
                  onClick={(e) => {
                    e.preventDefault();
                    jumpTo(s.id);
                  }}
                  className="hover:bg-muted focus-visible:ring-ring flex min-h-8 items-center gap-2 rounded-md px-2 text-sm transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none"
                >
                  {s.stopType && (
                    <LineBullet
                      type={s.stopType}
                      size={16}
                      number={stop}
                      hollow={stop === undefined}
                      decorative
                    />
                  )}
                  <span className="min-w-0 flex-1 truncate">{s.title}</span>
                  {t && (
                    <Lamp
                      tone={t}
                      label={t === "destructive" ? "Has problems" : "Has things to check"}
                    />
                  )}
                </a>
              </li>
            );
          })}
        </ol>
      </nav>

      <div className="min-w-0">
        {/* Choosing an option only picks it; Go moves there. Moving on
            every change would throw a keyboard user (arrow keys change a
            closed select at once) to the first section they pass. */}
        <div className="mb-6 flex flex-col gap-1.5 lg:hidden">
          <Label htmlFor="settings-jump">Jump to</Label>
          <div className="flex items-center gap-2">
            <Select
              id="settings-jump"
              className="min-w-0 flex-1"
              value={jump}
              onChange={(e) => setJump(e.target.value)}
            >
              <option value="">Choose a section</option>
              {sections.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.sub ? `Agent: ${s.title}` : s.title}
                </option>
              ))}
            </Select>
            <Button
              type="button"
              variant="outline"
              className="shrink-0"
              disabled={!jump}
              onClick={() => jumpTo(jump)}
            >
              Go
            </Button>
          </div>
        </div>

        {hasProblems && <div className="mb-8">{problems}</div>}

        {sections.map((s) => (
          <section
            key={s.id}
            aria-labelledby={headingId(s.id)}
            className="border-border border-t py-8 first-of-type:border-t-0 first-of-type:pt-0"
          >
            <SectionHeading
              id={headingId(s.id)}
              tabIndex={-1}
              level={2}
              title={s.title}
              description={s.description}
            />
            {s.body != null && <div className="mt-4">{s.body}</div>}
          </section>
        ))}
      </div>
    </div>
  );
}
