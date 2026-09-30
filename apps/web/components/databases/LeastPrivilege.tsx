"use client";

import { Check, ChevronRight, Copy } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { DbEngine } from "@/lib/api";

/** The role to create *before* connecting, per engine.
 *
 *  This guidance lived only in docs/DATABASE_ACCESS.md, which the running app
 *  cannot show. It was referenced as plain text, and hidden while the add
 *  form was open, which is exactly when someone is choosing a credential.
 *  Keep these in step with that document. `steps` engines have no code to
 *  run, so their body is shown as a list rather than a code block. */
const SNIPPETS: Record<DbEngine, { lang: "sql" | "js" | "steps"; body: string; note: string }> = {
  postgres: {
    lang: "sql",
    note: "ALTER DEFAULT PRIVILEGES is the line people miss: without it, tables created later are invisible to the assistant.",
    body: `CREATE ROLE assistant_ro LOGIN PASSWORD '<generated secret>'
  NOSUPERUSER NOCREATEDB NOCREATEROLE;
REVOKE ALL ON DATABASE appdb FROM PUBLIC;
GRANT CONNECT ON DATABASE appdb TO assistant_ro;
GRANT USAGE ON SCHEMA public TO assistant_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO assistant_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO assistant_ro;
REVOKE ALL ON TABLE salaries, api_keys FROM assistant_ro;
ALTER ROLE assistant_ro SET statement_timeout = '10s';`,
  },
  mysql: {
    lang: "sql",
    note: "MySQL cannot revoke one table after a wildcard grant, so for sensitive tables grant table by table instead.",
    body: `CREATE USER 'assistant_ro'@'%' IDENTIFIED BY '<generated secret>';
GRANT SELECT ON appdb.* TO 'assistant_ro'@'%';
-- or, to keep sensitive tables out, per table:
--   GRANT SELECT ON appdb.orders TO 'assistant_ro'@'%';
FLUSH PRIVILEGES;`,
  },
  mongodb: {
    lang: "js",
    note: "Use the read role, not readWrite or dbAdmin. The assistant's query checks already block writes; this makes it true at the server too.",
    body: `use admin
db.createUser({
  user: "assistant_ro",
  pwd: "<generated secret>",
  roles: [{ role: "read", db: "appdb" }]
})`,
  },
  sqlite: {
    lang: "steps",
    note: "SQLite has no roles: access is the file itself.",
    body: `Point the connection at a copy, or at a file on a read-only mount.
The file is opened read-only unless a write has been approved.
Keep it outside any folder the web server can serve.`,
  },
};

export function LeastPrivilege({ engine }: { engine: DbEngine }) {
  // Which engine's snippet was copied: a new engine shows a new snippet,
  // which has not been copied yet.
  const [copiedFor, setCopiedFor] = useState<DbEngine | null>(null);
  const copied = copiedFor === engine;
  const s = SNIPPETS[engine];

  async function copy() {
    try {
      await navigator.clipboard.writeText(s.body);
      setCopiedFor(engine);
    } catch {
      /* the snippet is selectable text as well */
    }
  }

  return (
    <details className="group border-border rounded-md border">
      <summary className="text-label focus-visible:ring-ring flex cursor-pointer list-none items-center gap-2 rounded-md px-3 py-2.5 font-medium focus-visible:ring-2 focus-visible:outline-none [&::-webkit-details-marker]:hidden">
        <ChevronRight
          aria-hidden
          className="text-muted-foreground size-4 shrink-0 transition-transform duration-120 ease-out group-open:rotate-90"
        />
        {engine === "sqlite"
          ? "Keep the database file read-only"
          : "Create a read-only role for this connection"}
      </summary>
      <div className="flex flex-col gap-3 px-3 pb-3">
        <p className="text-small text-muted-foreground max-w-[60ch]">
          The assistant&apos;s query checks are software and can have bugs; a grant is enforced by
          the database itself. Use both. {s.note}
        </p>
        {s.lang === "steps" ? (
          <ul className="text-small ml-4 flex list-disc flex-col gap-1">
            {s.body.split("\n").map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        ) : (
          <div className="relative">
            <pre
              aria-label={
                s.lang === "sql" ? "SQL to create the role" : "Command to create the user"
              }
              className="bg-muted text-code overflow-x-auto rounded-md p-3 pr-24 font-mono select-all"
            >
              {s.body}
            </pre>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="absolute top-2 right-2"
              onClick={() => void copy()}
            >
              {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
              {copied ? "Copied" : "Copy"}
            </Button>
            <span role="status" className="sr-only">
              {copied ? "Copied to the clipboard." : ""}
            </span>
          </div>
        )}
      </div>
    </details>
  );
}
