"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import {
  changesFor,
  compareGraphs,
  comparisonSummary,
  type GraphComparison,
} from "@/components/canvas/graph-diff";
import { stationLabel } from "@/components/canvas/graph-sync";
import { SUBAGENT_ROLE_NAME, TOOL_NAME } from "@/components/canvas/station";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { nodeTypeLabel } from "@/components/ui/line-bullet";
import { Loading } from "@/components/ui/loading";
import { SectionHeading } from "@/components/ui/section-heading";
import {
  ApiError,
  assistants,
  type AssistantVersion,
  type DiffEntry,
  type Graph,
  type VersionDiff,
} from "@/lib/api";
import { cn } from "@/lib/utils";

// The canvas is the largest thing on the page, and History is one tab of
// eight: loaded when a comparison is first drawn, not with the builder.
const Canvas = dynamic(() => import("@/components/canvas/Canvas").then((m) => m.Canvas), {
  ssr: false,
  loading: () => <Loading what="the comparison" rows={1} rowHeight={120} />,
});

/** What went wrong, then what to do: the server's message when it gave one. */
function failed(what: string, err: unknown, todo: string): string {
  const why =
    err instanceof ApiError
      ? err.message.trim().replace(/[.!?]+$/, "")
      : "The server couldn't be reached";
  return `${what} ${why}. ${todo}`;
}

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

/** Published versions, and what changed between any two of them.
 *
 *  The backend has listed versions and diffed them since task 1.2; nothing in
 *  the UI called either. A version is immutable, so this is read-only: pick
 *  two, see the two pipelines as one drawing with what changed marked on it
 *  (task 6.8), then the same changes as a list, and the config changes by
 *  setting. */
export function VersionHistory({
  assistantId,
  sourceLabels = {},
}: {
  assistantId: string;
  /** Names for the documents, databases and servers a station points at. */
  sourceLabels?: Record<string, string>;
}) {
  const [rows, setRows] = useState<AssistantVersion[]>([]);
  const [more, setMore] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  // Compare `from` with `to`; defaults to the latest version vs the one before.
  const [from, setFrom] = useState<number | null>(null);
  const [to, setTo] = useState<number | null>(null);
  const [diff, setDiff] = useState<VersionDiff | null>(null);
  // The two versions' graphs, for the drawing. The list and the table need
  // only the diff, so they show even if these fail to load.
  const [graphs, setGraphs] = useState<{ from: Graph; to: Graph } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);

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
      .catch((err) =>
        setError(
          failed("Couldn't load the published versions.", err, "Reload the page to try again."),
        ),
      )
      .finally(() => setLoaded(true));
  }, [assistantId]);

  const loadMore = useCallback(async () => {
    if (!more) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await assistants.versions(assistantId, more);
      setRows((r) => [...r, ...page.items]);
      setMore(page.next_cursor);
    } catch (err) {
      setMoreError(failed("Couldn't load older versions.", err, "Try again."));
    } finally {
      setLoadingMore(false);
    }
  }, [assistantId, more]);

  useEffect(() => {
    setGraphs(null);
    if (from === null || to === null || from === to) {
      setDiff(null);
      return;
    }
    setError(null);
    let live = true;
    Promise.all([assistants.version(assistantId, from), assistants.version(assistantId, to)])
      .then(([a, b]) => {
        if (live) setGraphs({ from: a.graph, to: b.graph });
      })
      .catch(() => {});
    assistants
      .diff(assistantId, from, to)
      .then((d) => {
        if (live) setDiff(d);
      })
      .catch((err) =>
        setError(
          failed(
            `Couldn't compare version ${from} with version ${to}.`,
            err,
            "Pick the versions again to retry.",
          ),
        ),
      );
    return () => {
      live = false;
    };
  }, [assistantId, from, to]);

  if (!loaded) {
    return (
      <div className="mx-auto w-full max-w-5xl px-4 py-6 md:px-8">
        <Loading what="published versions" rows={3} rowHeight={72} />
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-4 py-6 md:px-8 lg:flex-row lg:items-start">
        <section aria-labelledby="versions-heading" className="w-full shrink-0 lg:w-80">
          <SectionHeading
            id="versions-heading"
            level={2}
            title="Published versions"
            description={
              rows.length > 0
                ? "Pick a version on each side to compare. Versions never change once published."
                : undefined
            }
          />
          {rows.length === 0 && !error && (
            <EmptyState
              className="mt-4"
              title="Nothing published yet"
              description="Publish the draft to create version 1. Each version keeps the assistant as it was, so you can compare them here."
            />
          )}
          {rows.length > 0 && (
            <ul className="border-border mt-4 border-t">
              {rows.map((v) => (
                <li key={v.id} className="border-border flex items-center gap-3 border-b py-2.5">
                  <div className="min-w-0 flex-1">
                    <p className="num text-sm font-medium">Version {v.version_number}</p>
                    <p className="text-small text-muted-foreground num">{when(v.created_at)}</p>
                    <p
                      className={cn(
                        "text-small truncate",
                        v.note ? "text-foreground" : "text-muted-foreground",
                      )}
                      title={v.note || undefined}
                    >
                      {v.note || "No note"}
                    </p>
                  </div>
                  <div
                    role="group"
                    aria-label={`Compare version ${v.version_number}`}
                    className="border-field-border flex shrink-0 overflow-hidden rounded-md border"
                  >
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
                  </div>
                </li>
              ))}
            </ul>
          )}
          {more && (
            <Button
              variant="ghost"
              size="sm"
              className="mt-2"
              disabled={loadingMore}
              onClick={() => void loadMore()}
            >
              {loadingMore ? "Loading older versions…" : "Load older versions"}
            </Button>
          )}
          {moreError && (
            <Alert className="mt-2" role="alert">
              {moreError}
            </Alert>
          )}
        </section>

        <section aria-label="Changes" className="min-w-0 flex-1">
          {error && <Alert>{error}</Alert>}
          {rows.length === 1 && (
            <p className="text-muted-foreground max-w-[60ch] text-sm">
              Only one version so far. Publish again to have something to compare.
            </p>
          )}
          {from !== null && from === to && (
            <p className="text-muted-foreground text-sm">
              Pick two different versions to see what changed.
            </p>
          )}
          {diff && <DiffView diff={diff} graphs={graphs} sourceLabels={sourceLabels} />}
        </section>
      </div>
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
        "text-label focus-visible:ring-ring h-8 min-w-12 px-2.5 font-medium transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset",
        active
          ? "bg-primary text-primary-foreground"
          : "bg-card text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      {label}
    </button>
  );
}

/** A node in the diff, in words. The diff only carries ids and types, so a
 *  tool or subagent is named from its id and anything else by its type. */
function nodeName(id: string, type: string | undefined): string {
  const [prefix, rest] = id.split(":", 2);
  if (type === "tool" && rest) return TOOL_NAME[rest] ?? rest;
  if (type === "subagent" && rest) return SUBAGENT_ROLE_NAME[rest] ?? nodeTypeLabel(type);
  if (!type && prefix) return prefix;
  return nodeTypeLabel(type ?? "node");
}

const OP: Record<
  DiffEntry["op"],
  { variant: "success" | "destructive" | "warning"; word: string }
> = {
  added: { variant: "success", word: "Added" },
  removed: { variant: "destructive", word: "Removed" },
  changed: { variant: "warning", word: "Changed" },
};

const noop = () => {};

/** The two pipelines as one drawing (task 6.8). Read-only: stations can be
 *  reached and picked, from the keyboard too, and picking one narrows the
 *  settings table below to that station. */
function Comparison({
  comparison,
  sourceLabels,
  picked,
  onPick,
}: {
  comparison: GraphComparison;
  sourceLabels: Record<string, string>;
  picked: string | null;
  onPick: (id: string | null) => void;
}) {
  const marks = useMemo(
    () => ({ nodes: comparison.nodes, edges: comparison.edges, counts: comparison.counts }),
    [comparison],
  );
  return (
    <div className="flex flex-col gap-2">
      <div className="border-border h-[420px] overflow-hidden rounded-lg border">
        <Canvas
          graph={comparison.graph}
          schema={null}
          sourceLabels={sourceLabels}
          selectedId={picked}
          onSelect={onPick}
          onNodeMoved={noop}
          diff={marks}
          readOnly
          label="The two versions' pipelines, with what changed marked"
        />
      </div>
      <p className="text-small text-muted-foreground max-w-[70ch]">
        Tagged stations were added, removed or changed. A line on a green band was added; a dashed
        red line was removed. Everything in grey is the same in both versions. Pick a changed
        station to see only its settings below.
      </p>
    </div>
  );
}

function DiffView({
  diff,
  graphs,
  sourceLabels,
}: {
  diff: VersionDiff;
  graphs: { from: Graph; to: Graph } | null;
  sourceLabels: Record<string, string>;
}) {
  const g = diff.graph_diff;
  const name = (id: string) => nodeName(id, g.node_types[id]);
  const comparison = useMemo(
    () => (graphs ? compareGraphs(graphs.from, graphs.to, g) : null),
    [graphs, g],
  );
  const [picked, setPicked] = useState<string | null>(null);
  // A station picked in one comparison means nothing in the next.
  useEffect(() => setPicked(null), [diff]);
  const pickedNode = comparison?.graph.nodes.find((n) => n.id === picked) ?? null;
  const pickedChanges =
    comparison && pickedNode ? changesFor(g.nodes_changed, pickedNode.id, comparison.graph) : null;
  const summary = comparison ? comparisonSummary(comparison) : null;
  const noGraphChange =
    g.nodes_added.length +
      g.nodes_removed.length +
      g.nodes_changed.length +
      g.edges_added.length +
      g.edges_removed.length ===
    0;

  return (
    <div className="flex flex-col gap-8">
      <h2 className="text-h2 num font-semibold">
        Changes from version {diff.from_version} to version {diff.to_version}
      </h2>

      <section className="flex flex-col gap-3">
        <SectionHeading
          level={3}
          title="Pipeline"
          description={summary ? <span className="num">{summary}</span> : undefined}
        />
        {noGraphChange && <p className="text-muted-foreground text-sm">No pipeline changes.</p>}
        {!noGraphChange && comparison && (
          <Comparison
            comparison={comparison}
            sourceLabels={sourceLabels}
            picked={picked}
            onPick={setPicked}
          />
        )}
        {!noGraphChange && (
          <ul className="border-border border-t text-sm">
            {g.nodes_added.map((id) => (
              <GraphRow key={`+${id}`} variant="success" word="Added">
                {name(id)}
              </GraphRow>
            ))}
            {g.nodes_removed.map((id) => (
              <GraphRow key={`-${id}`} variant="destructive" word="Removed">
                {name(id)}
              </GraphRow>
            ))}
            {g.edges_added.map(([s, t]) => (
              <GraphRow key={`+${s}>${t}`} variant="success" word="Connected">
                {name(s)} to {name(t)}
              </GraphRow>
            ))}
            {g.edges_removed.map(([s, t]) => (
              <GraphRow key={`-${s}>${t}`} variant="destructive" word="Disconnected">
                {name(s)} from {name(t)}
              </GraphRow>
            ))}
          </ul>
        )}
        {pickedNode && pickedChanges && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1" role="status">
            <p className="text-sm">
              {pickedChanges.length > 0
                ? `Showing the changed settings of ${stationLabel(pickedNode, sourceLabels)}.`
                : `${stationLabel(pickedNode, sourceLabels)} has no changed settings.`}
            </p>
            <Button variant="link" size="sm" onClick={() => setPicked(null)}>
              Show all
            </Button>
          </div>
        )}
        {(pickedChanges ?? g.nodes_changed).length > 0 && (
          <ChangeTable entries={pickedChanges ?? g.nodes_changed} />
        )}
      </section>

      <section className="flex flex-col gap-3">
        <SectionHeading level={3} title="Configuration" />
        {diff.config_diff.length === 0 ? (
          <p className="text-muted-foreground text-sm">No configuration changes.</p>
        ) : (
          <ChangeTable entries={diff.config_diff} />
        )}
      </section>
    </div>
  );
}

function GraphRow({
  variant,
  word,
  children,
}: {
  variant: "success" | "destructive";
  word: string;
  children: ReactNode;
}) {
  return (
    <li className="border-border flex items-center gap-3 border-b py-2">
      <Badge variant={variant} className="w-30 justify-start">
        {word}
      </Badge>
      <span className="min-w-0">{children}</span>
    </li>
  );
}

function ChangeTable({ entries }: { entries: DiffEntry[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] table-fixed text-left text-sm">
        <thead className="text-muted-foreground text-small">
          <tr className="border-border border-b">
            <th scope="col" className="w-2/5 py-2 pr-3 font-medium">
              Setting
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Before
            </th>
            <th scope="col" className="py-2 font-medium">
              After
            </th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={`${e.op}:${e.path}`} className="border-border border-b align-top">
              <td className="py-2 pr-3">
                <div className="flex flex-col items-start gap-1">
                  <Badge variant={OP[e.op].variant}>{OP[e.op].word}</Badge>
                  <code className="text-code font-mono break-all">{e.path}</code>
                </div>
              </td>
              <td className="py-2 pr-3">
                <Value v={e.op === "added" ? undefined : e.before} />
              </td>
              <td className="py-2">
                <Value v={e.op === "removed" ? undefined : e.after} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Value({ v }: { v: unknown }) {
  if (v === undefined) return <span className="text-muted-foreground text-small">Not set</span>;
  const text = typeof v === "string" ? v : JSON.stringify(v);
  return (
    <code className="bg-muted text-code block max-h-40 overflow-auto rounded-sm px-1.5 py-0.5 font-mono break-all whitespace-pre-wrap">
      {text}
    </code>
  );
}
