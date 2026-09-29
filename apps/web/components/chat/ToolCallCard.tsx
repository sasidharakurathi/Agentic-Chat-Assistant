"use client";

import { useState } from "react";

import { Markdown } from "@/components/chat/Markdown";
import { toolLabel, toolShortName } from "@/lib/tool-label";
import { permissionText, type ToolCallView } from "@/lib/tool-tree";
import { cn } from "@/lib/utils";

export type { ToolCallView } from "@/lib/tool-tree";

/** `nested`: calls a subagent made under this (delegation) call. */
export function ToolCallCard({
  call,
  nested = [],
}: {
  call: ToolCallView;
  nested?: ToolCallView[];
}) {
  const [open, setOpen] = useState(false);
  const delegated = Boolean(call.subagent_text) || nested.length > 0;
  // Defensive: `blocks` is a growing mixed list and this app has no error
  // boundary on the chat route, so one bad deref here blanks the whole
  // conversation rather than degrading a single card.
  const short = toolShortName(call.name ?? "tool");
  const label = toolLabel(call.name ?? "tool");
  const sql = typeof call.input?.sql === "string" ? (call.input.sql as string) : null;
  const rows = parseRows(call.output);
  return (
    <div className="border-border bg-muted/40 rounded-md border text-xs">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-1.5"
      >
        <span className="font-medium">
          🔧 {label}
          {delegated && <span className="text-muted-foreground ml-2">subagent</span>}
          {call.status && (
            <span
              className={cn(
                "ml-2",
                call.status === "success" ? "text-emerald-600" : "text-destructive",
              )}
            >
              {call.status}
            </span>
          )}
        </span>
        <span className="text-muted-foreground">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div className="border-border space-y-2 border-t px-3 py-2">
          {permissionText(call.permission) && (
            <p className="text-muted-foreground">{permissionText(call.permission)}.</p>
          )}
          {sql ? (
            // The run trace shows the SQL that ran (task 3.9), not a JSON blob
            // with the statement buried inside it.
            <div>
              <div className="text-muted-foreground">sql</div>
              <pre className="bg-muted/50 overflow-auto rounded px-2 py-1 font-mono whitespace-pre-wrap">
                {sql}
              </pre>
            </div>
          ) : (
            <div>
              <div className="text-muted-foreground">input</div>
              <pre className="overflow-auto">{JSON.stringify(call.input, null, 2)}</pre>
            </div>
          )}
          {delegated && (
            // What the subagent did on the way: its own notes and the calls it
            // made. Kept here, never in the answer (task 2.10).
            <div className="border-border space-y-1.5 border-l-2 pl-2">
              <div className="text-muted-foreground">subagent</div>
              {call.subagent_text && (
                <p className="text-muted-foreground whitespace-pre-wrap italic">
                  {call.subagent_text}
                </p>
              )}
              {nested.map((c) => (
                <ToolCallCard key={c.id} call={c} />
              ))}
            </div>
          )}
          {call.output != null && (
            <div>
              <div className="text-muted-foreground">output</div>
              {rows ? (
                <ResultTable rows={rows} />
              ) : short === "kb_search" ? (
                // Retrieved passages are documents, often Markdown: render them.
                <Markdown text={call.output} className="max-h-96 overflow-auto" />
              ) : (
                <pre className="overflow-auto whitespace-pre-wrap">{call.output}</pre>
              )}
            </div>
          )}
        </div>
      )}
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
    return <p className="text-muted-foreground">no rows</p>;
  }
  return (
    <div className="max-h-64 overflow-auto">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-border border-b">
            {rows.columns.map((c) => (
              <th key={c} className="text-muted-foreground px-2 py-1 font-medium">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.rows.map((row, i) => (
            <tr key={i} className="border-border/50 border-b last:border-0">
              {row.map((cell, j) => (
                <td key={j} className="px-2 py-1 font-mono">
                  {cell === null ? (
                    <span className="text-muted-foreground">null</span>
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
