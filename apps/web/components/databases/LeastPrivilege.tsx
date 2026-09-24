"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { DbEngine } from "@/lib/api";

/** The role to create *before* connecting, per engine.
 *
 *  This guidance lived only in docs/DATABASE_ACCESS.md, which the running app
 *  cannot show. It was referenced as plain text, and hidden while the add
 *  form was open, which is exactly when someone is choosing a credential.
 *  Keep these in step with that document. */
const SNIPPETS: Record<DbEngine, { lang: string; body: string; note: string }> = {
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
    note: "MySQL cannot revoke one table after a wildcard grant: for sensitive tables, grant table by table instead.",
    body: `CREATE USER 'assistant_ro'@'%' IDENTIFIED BY '<generated secret>';
GRANT SELECT ON appdb.* TO 'assistant_ro'@'%';
-- or, to keep sensitive tables out, per table:
--   GRANT SELECT ON appdb.orders TO 'assistant_ro'@'%';
FLUSH PRIVILEGES;`,
  },
  mongodb: {
    lang: "js",
    note: "The read role, not readWrite or dbAdmin. The agent's pipeline screening already blocks writes; this makes it true at the server too.",
    body: `use admin
db.createUser({
  user: "assistant_ro",
  pwd: "<generated secret>",
  roles: [{ role: "read", db: "appdb" }]
})`,
  },
  sqlite: {
    lang: "text",
    note: "SQLite has no roles: access is the file itself.",
    body: `- Point the connection at a copy, or at a file on a read-only mount.
- The file is opened read-only unless a write has been approved.
- Keep it outside any directory the web server can serve.`,
  },
};

export function LeastPrivilege({ engine }: { engine: DbEngine }) {
  const [copied, setCopied] = useState(false);
  const s = SNIPPETS[engine];

  async function copy() {
    try {
      await navigator.clipboard.writeText(s.body);
      setCopied(true);
    } catch {
      /* the snippet is selectable text as well */
    }
  }

  return (
    <details className="border-border rounded-md border px-3 py-2 text-xs">
      <summary className="cursor-pointer font-medium">
        Use a least-privilege {engine === "sqlite" ? "file" : "role"} for this connection
      </summary>
      <p className="text-muted-foreground mt-2">
        The query guard is software and can have bugs; a database grant is enforced by the database
        itself. Use both. {s.note}
      </p>
      <div className="relative mt-2">
        <pre className="bg-muted overflow-x-auto rounded p-2 pr-16 font-mono text-[11px] leading-relaxed select-all">
          {s.body}
        </pre>
        {engine !== "sqlite" && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="absolute top-1.5 right-1.5 h-6 px-2 text-[11px]"
            onClick={() => void copy()}
          >
            {copied ? "Copied" : "Copy"}
          </Button>
        )}
      </div>
    </details>
  );
}
