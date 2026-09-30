"use client";

import { ChevronRight } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { CodeBlock } from "@/components/chat/CodeBlock";
import { Markdown } from "@/components/chat/Markdown";
import { Lamp } from "@/components/ui/lamp";
import { LineBullet } from "@/components/ui/line-bullet";
import { formatMs } from "@/lib/run-trace";
import { subagentLabel, toolNodeType, toolSentence, toolShortName } from "@/lib/tool-label";
import { nestCalls, permissionText, type ToolCallView } from "@/lib/tool-tree";
import { cn } from "@/lib/utils";

export type { ToolCallView } from "@/lib/tool-tree";

/** The tool steps of one answer, as a vertical step gutter (docs/DESIGN.md
 *  section 7): each step's bullet in its capability's line colour, joined by
 *  a thin rule. A subagent's own calls sit under its delegation step. No
 *  stop numbers here: those belong to the pipeline, not to a turn. */
export function ToolSteps({
  calls,
  live = false,
  label = "Steps",
}: {
  calls: ToolCallView[];
  /** The turn is still streaming: a call with no result yet is running. */
  live?: boolean;
  label?: string;
}) {
  const { top, children } = nestCalls(calls);
  if (top.length === 0) return null;
  return (
    <StepList label={label}>
      {top.map((t, i) => (
        <ToolCallCard key={t.id ?? i} call={t} nested={children.get(t.id)} live={live} />
      ))}
    </StepList>
  );
}

function StepList({ label, children }: { label: string; children: ReactNode[] }) {
  return (
    <ol aria-label={label} className="flex flex-col">
      {children.map((child, i) => (
        <li key={i} className="relative pb-1 last:pb-0">
          {i < children.length - 1 && (
            // The gutter: from under this bullet to the top of the next one.
            <span
              aria-hidden
              className="bg-line-unlit absolute top-[26px] -bottom-1.5 left-[9px] w-0.5"
            />
          )}
          {child}
        </li>
      ))}
    </ol>
  );
}

/** Whether a finished call went wrong, in a word. */
function failedWord(status?: string): string | null {
  if (!status || status === "success") return null;
  return status === "error" ? "Failed" : "Didn't finish";
}

/** One tool step: its bullet, what it did as a plain sentence, how long it
 *  took, and (expanded) exactly what it sent and got back.
 *
 *  `nested`: calls a subagent made under this (delegation) call. */
export function ToolCallCard({
  call,
  nested = [],
  live = false,
}: {
  call: ToolCallView;
  nested?: ToolCallView[];
  /** Part of a turn still streaming, so a call without a result is running. */
  live?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const delegated = Boolean(call.subagent_text) || nested.length > 0;
  // Defensive: `blocks` is a growing mixed list and this app has no error
  // boundary on the chat route, so one bad deref here blanks the whole
  // conversation rather than degrading a single card.
  const name = call.name ?? "tool";
  const input = call.input && typeof call.input === "object" ? call.input : {};
  const short = toolShortName(name);
  const role = subagentLabel(name, input);
  const sentence = toolSentence(name, input);
  const sql = typeof input.sql === "string" ? (input.sql as string) : null;
  const rows = parseRows(call.output);
  const running = live && !call.status && call.output == null;
  const failed = failedWord(call.status);
  const permission = permissionText(call.permission);
  const hasInput = Object.keys(input).length > 0;

  return (
    <div className="flex items-start gap-3">
      <LineBullet type={toolNodeType(name)} size={20} className="relative mt-1.5" />
      <div className="min-w-0 flex-1">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={bodyId}
          className="group hover:bg-muted focus-visible:ring-ring -mx-2 flex min-h-8 w-[calc(100%+1rem)] items-center gap-x-3 gap-y-1 rounded-md px-2 py-1 text-left transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none"
        >
          <span className="min-w-0 flex-1 break-words">
            {sentence}
            {delegated && !role && (
              <span className="text-small text-muted-foreground ml-2">by a subagent</span>
            )}
          </span>
          {running && (
            <span className="text-small text-muted-foreground inline-flex shrink-0 items-center gap-1.5">
              <Lamp tone="neutral" live />
              Running
            </span>
          )}
          {failed && (
            <span className="text-small inline-flex shrink-0 items-center gap-1.5 font-medium">
              <Lamp tone="destructive" />
              {failed}
            </span>
          )}
          {call.duration_ms != null && (
            <span className="num text-small text-muted-foreground shrink-0 text-right">
              {formatMs(call.duration_ms)}
            </span>
          )}
          <ChevronRight
            aria-hidden
            className="text-muted-foreground size-4 shrink-0 transition-transform duration-120 ease-out group-aria-expanded:rotate-90 motion-reduce:transition-none"
          />
        </button>

        {open && (
          <div id={bodyId} className="mt-1 mb-3 flex flex-col gap-3">
            {permission && <p className="text-small text-muted-foreground">{permission}.</p>}
            {sql ? (
              // The SQL that ran, not a JSON blob with the statement buried
              // inside it.
              <Part title="SQL">
                <CodeBlock code={sql} label="SQL" />
              </Part>
            ) : hasInput ? (
              <Part title="Input">
                <CodeBlock code={JSON.stringify(input, null, 2)} label="Input" />
              </Part>
            ) : (
              <p className="text-small text-muted-foreground">It took no input.</p>
            )}
            {delegated && (
              // What the subagent did on the way: its own notes and the calls
              // it made. Kept here, never in the answer.
              <Part title="What the subagent did">
                <div className="border-border flex flex-col gap-2 border-l-2 pl-3">
                  {call.subagent_text && (
                    <p className="text-muted-foreground max-w-[68ch] break-words whitespace-pre-wrap">
                      {call.subagent_text}
                    </p>
                  )}
                  {nested.length > 0 && (
                    <StepList label="Subagent steps">
                      {nested.map((c, i) => (
                        <ToolCallCard key={c.id ?? i} call={c} live={live} />
                      ))}
                    </StepList>
                  )}
                </div>
              </Part>
            )}
            {call.output != null && (
              <Part title="Result">
                {rows ? (
                  <ResultTable rows={rows} />
                ) : short === "kb_search" ? (
                  // Retrieved passages are documents, often Markdown: render them.
                  <Markdown
                    text={call.output}
                    className="text-body border-border max-h-96 max-w-none overflow-auto rounded-md border p-3"
                  />
                ) : (
                  <CodeBlock code={call.output} label="Result" />
                )}
              </Part>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <p className="text-label text-muted-foreground font-medium">{title}</p>
      {children}
    </div>
  );
}

/** Pull `{columns, rows}` out of a sql/mongo tool result so it can render as a
 *  table. The tool prints a couple of human-readable lines and then one JSON
 *  object; anything that doesn't match falls back to the raw text. */
function parseRows(output?: string): { columns: string[]; rows: unknown[][] } | null {
  if (!output) return null;
  const start = output.indexOf("{");
  if (start < 0) return null;
  try {
    const parsed = JSON.parse(output.slice(start));
    const columns = parsed.columns ?? parsed.fields;
    if (Array.isArray(columns) && Array.isArray(parsed.rows)) {
      return { columns: columns.map(String), rows: parsed.rows };
    }
  } catch {
    /* not a result set — show the text */
  }
  return null;
}

function ResultTable({ rows }: { rows: { columns: string[]; rows: unknown[][] } }) {
  if (rows.rows.length === 0) {
    return <p className="text-small text-muted-foreground">No rows came back.</p>;
  }
  return (
    <div
      tabIndex={0}
      role="region"
      aria-label={`Result, ${rows.rows.length} ${rows.rows.length === 1 ? "row" : "rows"}`}
      className="border-border focus-visible:ring-ring max-h-64 overflow-auto rounded-md border focus-visible:ring-2 focus-visible:outline-none"
    >
      <table className="text-small w-full border-collapse text-left">
        <thead className="bg-card sticky top-0">
          <tr className="border-border border-b">
            {rows.columns.map((c) => (
              <th key={c} scope="col" className="px-2.5 py-1.5 font-semibold whitespace-nowrap">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.rows.map((row, i) => (
            <tr key={i} className="border-border border-b last:border-0">
              {(Array.isArray(row) ? row : [row]).map((cell, j) => (
                <td
                  key={j}
                  className={cn(
                    "px-2.5 py-1.5 align-top",
                    typeof cell === "number" && "num text-right",
                  )}
                >
                  {cell === null ? (
                    <span className="text-muted-foreground">null</span>
                  ) : typeof cell === "object" ? (
                    JSON.stringify(cell)
                  ) : (
                    String(cell)
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
