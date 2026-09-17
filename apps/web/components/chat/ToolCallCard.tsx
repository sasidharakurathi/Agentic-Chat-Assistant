"use client";

import { useState } from "react";

import { cn } from "@/lib/utils";

export type ToolCallView = {
  id: string;
  name: string;
  input: Record<string, unknown>;
  status?: string;
  output?: string;
};

export function ToolCallCard({ call }: { call: ToolCallView }) {
  const [open, setOpen] = useState(false);
  const short = call.name.replace(/^mcp__caps__/, "");
  return (
    <div className="border-border bg-muted/40 rounded-md border text-xs">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-1.5"
      >
        <span className="font-medium">
          🔧 {short}
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
          <div>
            <div className="text-muted-foreground">input</div>
            <pre className="overflow-auto">{JSON.stringify(call.input, null, 2)}</pre>
          </div>
          {call.output != null && (
            <div>
              <div className="text-muted-foreground">output</div>
              <pre className="overflow-auto whitespace-pre-wrap">{call.output}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
