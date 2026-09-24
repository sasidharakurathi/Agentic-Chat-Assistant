"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  assistants,
  type AssistantVersion,
  type DiffEntry,
  type VersionDiff,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/** Published versions, and what changed between any two of them.
 *
 *  The backend has listed versions and diffed them since task 1.2; nothing in
 *  the UI called either. A version is immutable, so this is read-only: pick
 *  two, see the config changes by dotted path and the graph changes by node
 *  and edge. */
export function VersionHistory({ assistantId }: { assistantId: string }) {
  const [rows, setRows] = useState<AssistantVersion[]>([]);
  const [more, setMore] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  // Compare `from` → `to`; defaults to the latest version vs the one before.
  const [from, setFrom] = useState<number | null>(null);
  const [to, setTo] = useState<number | null>(null);
  const [diff, setDiff] = useState<VersionDiff | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    assistants
      .versions(assistantId)
      .then((page) => {
        setRows(page.items);
        setMore(page.next_cursor);
        if (page.items.length >= 2) {
          setTo(page.items[0].version_number);
          setFrom(page.items[1].version_number);
        }
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load versions"))
      .finally(() => setLoaded(true));
  }, [assistantId]);

  const loadMore = useCallback(async () => {
    if (!more) return;
    const page = await assistants.versions(assistantId, more);
    setRows((r) => [...r, ...page.items]);
    setMore(page.next_cursor);
  }, [assistantId, more]);

  useEffect(() => {
    if (from === null || to === null || from === to) {
      setDiff(null);
      return;
    }
    setError(null);
    assistants
      .diff(assistantId, from, to)
      .then(setDiff)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not compare"));
  }, [assistantId, from, to]);

  if (!loaded) return <div className="text-muted-foreground p-6 text-sm">Loading…</div>;

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-5xl flex-1 gap-6 overflow-auto p-6">
      <section className="w-72 shrink-0">
        <h2 className="text-sm font-semibold">Published versions</h2>
        {rows.length === 0 && (
          <p className="text-muted-foreground mt-2 text-sm">
            Nothing published yet. Publish to create version 1.
          </p>
        )}
        {rows.length > 0 && (
          <p className="text-muted-foreground mt-1 text-xs">
            Pick a version on each side to compare. Versions never change once published.
          </p>
        )}
        <ul className="mt-3 flex flex-col gap-1">
          {rows.map((v) => (
            <li
              key={v.id}
              className="border-border flex items-center gap-2 rounded-md border px-2 py-1.5 text-sm"
            >
              <div className="min-w-0 flex-1">
                <div className="font-medium">v{v.version_number}</div>
                <div className="text-muted-foreground truncate text-xs">
                  {v.note || "No note"} · {new Date(v.created_at).toLocaleString()}
                </div>
              </div>
              <Pick
                label="From"
                active={from === v.version_number}
                onClick={() => setFrom(v.version_number)}
              />
              <Pick
                label="To"
                active={to === v.version_number}
                onClick={() => setTo(v.version_number)}
              />
            </li>
          ))}
        </ul>
        {more && (
          <Button variant="ghost" size="sm" className="mt-2" onClick={() => void loadMore()}>
            Load older versions
          </Button>
        )}
      </section>

      <section className="min-w-0 flex-1">
        {error && (
          <p className="text-destructive text-sm" role="alert">
            {error}
          </p>
        )}
        {rows.length === 1 && (
          <p className="text-muted-foreground text-sm">
            Only one version so far. Publish again to have something to compare.
          </p>
        )}
        {from !== null && from === to && (
          <p className="text-muted-foreground text-sm">Pick two different versions.</p>
        )}
        {diff && <DiffView diff={diff} />}
      </section>
    </div>
  );
}

function Pick({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "rounded px-1.5 py-0.5 text-xs",
        active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted",
      )}
    >
      {label}
    </button>
  );
}

function DiffView({ diff }: { diff: VersionDiff }) {
  const g = diff.graph_diff;
  const label = (id: string) => `${(g.node_types[id] ?? "node").replace("_", " ")} “${id}”`;
  const noGraphChange =
    g.nodes_added.length +
      g.nodes_removed.length +
      g.nodes_changed.length +
      g.edges_added.length +
      g.edges_removed.length ===
    0;

  return (
    <div className="flex flex-col gap-6">
      <h2 className="text-sm font-semibold">
        v{diff.from_version} → v{diff.to_version}
      </h2>

      <div>
        <h3 className="text-muted-foreground mb-2 text-xs font-semibold tracking-wide uppercase">
          Configuration
        </h3>
        {diff.config_diff.length === 0 ? (
          <p className="text-muted-foreground text-sm">No configuration changes.</p>
        ) : (
          <ChangeTable entries={diff.config_diff} />
        )}
      </div>

      <div>
        <h3 className="text-muted-foreground mb-2 text-xs font-semibold tracking-wide uppercase">
          Pipeline graph
        </h3>
        {noGraphChange && <p className="text-muted-foreground text-sm">No graph changes.</p>}
        <ul className="flex flex-col gap-1 text-sm">
          {g.nodes_added.map((id) => (
            <li key={`+${id}`}>
              <Badge variant="success">added</Badge> {label(id)}
            </li>
          ))}
          {g.nodes_removed.map((id) => (
            <li key={`-${id}`}>
              <Badge variant="destructive">removed</Badge> {label(id)}
            </li>
          ))}
          {g.edges_added.map(([s, t]) => (
            <li key={`+${s}>${t}`}>
              <Badge variant="success">wired</Badge> {label(s)} → {label(t)}
            </li>
          ))}
          {g.edges_removed.map(([s, t]) => (
            <li key={`-${s}>${t}`}>
              <Badge variant="destructive">unwired</Badge> {label(s)} → {label(t)}
            </li>
          ))}
        </ul>
        {g.nodes_changed.length > 0 && (
          <div className="mt-3">
            <ChangeTable entries={g.nodes_changed} />
          </div>
        )}
      </div>
    </div>
  );
}

const OP_BADGE = { added: "success", removed: "destructive", changed: "warning" } as const;

function ChangeTable({ entries }: { entries: DiffEntry[] }) {
  return (
    <table className="w-full table-fixed text-left text-xs">
      <thead className="text-muted-foreground">
        <tr>
          <th className="w-1/3 py-1 font-medium">Setting</th>
          <th className="py-1 font-medium">Before</th>
          <th className="py-1 font-medium">After</th>
        </tr>
      </thead>
      <tbody className="divide-border divide-y">
        {entries.map((e) => (
          <tr key={`${e.op}:${e.path}`} className="align-top">
            <td className="py-1.5 pr-2">
              <Badge variant={OP_BADGE[e.op]}>{e.op}</Badge>{" "}
              <code className="break-all">{e.path}</code>
            </td>
            <td className="py-1.5 pr-2">
              <Value v={e.op === "added" ? undefined : e.before} />
            </td>
            <td className="py-1.5">
              <Value v={e.op === "removed" ? undefined : e.after} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Value({ v }: { v: unknown }) {
  if (v === undefined) return <span className="text-muted-foreground">—</span>;
  const text = typeof v === "string" ? v : JSON.stringify(v);
  return (
    <code className="bg-muted block max-h-40 overflow-auto rounded px-1.5 py-0.5 break-all whitespace-pre-wrap">
      {text}
    </code>
  );
}
