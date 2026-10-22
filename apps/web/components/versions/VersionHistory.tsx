"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
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
import { Button, buttonVariants } from "@/components/ui/button";
import { useConfirm, usePrompt } from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { nodeTypeLabel } from "@/components/ui/line-bullet";
import { Loading } from "@/components/ui/loading";
import { SectionHeading } from "@/components/ui/section-heading";
import { OfficialToggle, VersionListingSummary } from "@/components/listing/VersionListing";
import { ActionApprovers } from "@/components/versions/ActionApprovers";
import { ApprovalEvidence } from "@/components/versions/ApprovalEvidence";
import { AssistantEditors } from "@/components/versions/AssistantEditors";
import { useToast } from "@/components/ui/toast";
import {
  ApiError,
  assistants,
  orgs,
  type AssistantDetail,
  type AssistantVersion,
  type DiffEntry,
  type Graph,
  type VersionDiff,
} from "@/lib/api";
import {
  VERSION_STATUS_WORD,
  approveRule,
  canEditAssistant,
  isAdminRole,
  previewHref,
  reviewersOf,
  versionActions,
  type VersionStatus,
} from "@/lib/assistant-lifecycle";
import { useAuth } from "@/lib/auth";
import { compareManifests, type Manifest } from "@/lib/manifest-diff";
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

const STATUS_TONE: Record<VersionStatus, "success" | "warning" | "destructive" | "muted"> = {
  submitted: "warning",
  approved: "success",
  rejected: "destructive",
  superseded: "muted",
  withdrawn: "muted",
  rolled_back: "destructive",
};

/** Versions, their review, and what changed between any two of them.
 *
 *  A version never changes once submitted. An admin approves it before Chat
 *  answers from it (Phase 7b.1): here they approve or reject it, roll back
 *  to an earlier approved one, and pause the assistant. Its builders can
 *  take back a submission and preview any version. Pick two to see the two
 *  pipelines as one drawing with what changed marked on it (task 6.8), then
 *  the same changes as a list, and the config changes by setting. */
export function VersionHistory({
  assistant,
  sourceLabels = {},
  onChanged,
}: {
  assistant: AssistantDetail;
  /** Names for the documents, databases and servers a station points at. */
  sourceLabels?: Record<string, string>;
  /** The assistant's state changed (approved, rolled back, paused). */
  onChanged: () => Promise<void>;
}) {
  const assistantId = assistant.id;
  const { user, activeRole, activeOrgId } = useAuth();
  const admin = isAdminRole(activeRole);
  const editor = canEditAssistant(assistant, user?.id, activeRole);
  // Who else could approve (Phase 7b.3): nobody approves their own work
  // while another active admin or the owner could.
  const [reviewers, setReviewers] = useState<string[]>([]);
  useEffect(() => {
    if (!admin || !activeOrgId) return;
    orgs
      .members(activeOrgId)
      .then((page) => setReviewers(reviewersOf(page.items)))
      .catch(() => {});
  }, [admin, activeOrgId]);
  const ruleFor = (v: AssistantVersion) =>
    approveRule({
      me: user?.id,
      role: activeRole,
      submittedBy: v.created_by,
      builtBy: assistant.created_by,
      reviewers,
    });
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
  // What each was submitted with: knowledge, connections, tools (7b.1).
  const [manifests, setManifests] = useState<{ from: Manifest | null; to: Manifest | null } | null>(
    null,
  );
  // What has changed since the right-hand version was submitted.
  const [drift, setDrift] = useState<{ number: number; items: string[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);

  const loadVersions = useCallback(
    () =>
      assistants
        .versions(assistantId)
        .then((page) => {
          setRows(page.items);
          setMore(page.next_cursor);
          if (page.items.length >= 2) {
            setTo((t) => t ?? page.items[0].version_number);
            setFrom((f) => f ?? page.items[1].version_number);
          }
        })
        .catch((err) =>
          setError(failed("Couldn't load the versions.", err, "Reload the page to try again.")),
        )
        .finally(() => setLoaded(true)),
    [assistantId],
  );

  useEffect(() => {
    void loadVersions();
  }, [loadVersions]);

  // ── review, rollback, pause ──
  const confirm = useConfirm();
  const ask = usePrompt();
  const toast = useToast();
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const act = useCallback(
    async (what: string, run: () => Promise<unknown>, done: string) => {
      setActing(true);
      setActionError(null);
      try {
        await run();
        await Promise.all([loadVersions(), onChanged()]);
        toast(done, "success");
      } catch (err) {
        setActionError(failed(`Couldn't ${what}.`, err, "Reload the page, then try again."));
      } finally {
        setActing(false);
      }
    },
    [loadVersions, onChanged, toast],
  );

  const liveNumber = rows.find((v) => v.id === assistant.current_version_id)?.version_number;

  const onAction = async (action: ReturnType<typeof versionActions>[number], n: number) => {
    if (action === "approve") {
      const version = rows.find((r) => r.version_number === n);
      const own = version !== undefined && ruleFor(version) === "needs_note";
      const note = await ask({
        title: own ? `Approve your own version ${n}?` : `Approve version ${n}?`,
        description: own
          ? "Nobody else can review it, so you may approve it yourself. Say why it is ready: the note is kept with the approval, marked as self-approved."
          : "It becomes the live version: new conversations answer from it. Conversations already running stay on the version they started on.",
        label: own ? "Why it is ready" : "Note for the builder (optional)",
        confirmLabel: "Approve",
      });
      if (note === null) return;
      if (own && !note.trim()) {
        setActionError("Approving your own version needs a note saying why it is ready.");
        return;
      }
      await act(
        "approve the version",
        () => assistants.approve(assistantId, n, note.trim()),
        `Approved version ${n}`,
      );
    } else if (action === "reject") {
      const note = (
        await ask({
          title: `Reject version ${n}?`,
          description: "The builder reads your reason, so say what to change.",
          label: "Reason",
          confirmLabel: "Reject",
        })
      )?.trim();
      if (note === undefined) return;
      if (!note) {
        setActionError("A rejection needs a reason, so the builder knows what to change.");
        return;
      }
      await act(
        "reject the version",
        () => assistants.reject(assistantId, n, note),
        `Rejected version ${n}`,
      );
    } else if (action === "withdraw") {
      const sure = await confirm({
        title: `Withdraw version ${n}?`,
        description: "It leaves the review queue. Submit again when it is ready.",
        confirmLabel: "Withdraw",
      });
      if (!sure) return;
      await act(
        "withdraw the version",
        () => assistants.withdraw(assistantId, n),
        `Withdrew version ${n}`,
      );
    } else if (action === "make-live") {
      const sure = await confirm({
        title: `Roll back to version ${n}?`,
        description:
          liveNumber !== undefined
            ? `Version ${liveNumber} stops answering and can't go live again without a new submission. Its conversations move to version ${n} on their next message.`
            : `Version ${n} becomes the live version.`,
        confirmLabel: "Roll back",
        destructive: true,
      });
      if (!sure) return;
      await act(
        "roll back",
        () => assistants.makeLive(assistantId, n),
        `Version ${n} is live again`,
      );
    }
  };

  const pause = async () => {
    const reason = (
      await ask({
        title: "Pause this assistant?",
        description:
          "Nothing answers until it is reopened, previews included. Answers being written stop, and approvals waiting in its conversations expire.",
        label: "Reason",
        confirmLabel: "Pause",
      })
    )?.trim();
    if (reason === undefined) return;
    if (!reason) {
      setActionError("Pausing needs a reason, for the audit log and the builder.");
      return;
    }
    await act(
      "pause the assistant",
      () => assistants.archive(assistantId, reason),
      "Paused the assistant",
    );
  };

  const reopen = () =>
    act("reopen the assistant", () => assistants.unarchive(assistantId), "Reopened the assistant");

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
    setDrift(null);
    if (to === null) return;
    let live = true;
    assistants
      .drift(assistantId, to)
      .then((d) => {
        if (live) setDrift({ number: to, items: d.drift.map((x) => x.message) });
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [assistantId, to, rows]);

  useEffect(() => {
    setGraphs(null);
    setManifests(null);
    if (from === null || to === null || from === to) {
      setDiff(null);
      return;
    }
    setError(null);
    let live = true;
    Promise.all([assistants.version(assistantId, from), assistants.version(assistantId, to)])
      .then(([a, b]) => {
        if (!live) return;
        setGraphs({ from: a.graph, to: b.graph });
        setManifests({
          from: (a.manifest ?? null) as Manifest | null,
          to: (b.manifest ?? null) as Manifest | null,
        });
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
        <Loading what="versions" rows={3} rowHeight={72} />
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
            title="Versions"
            description={
              rows.length > 0
                ? "A version never changes once submitted. Chat answers only from the one an admin approved. Pick a version on each side to compare."
                : undefined
            }
          />
          <LiveState
            status={assistant.status}
            liveNumber={liveNumber}
            admin={admin}
            busy={acting}
            onPause={() => void pause()}
            onReopen={() => void reopen()}
          />
          {actionError && (
            <Alert className="mt-3" role="alert">
              {actionError}
            </Alert>
          )}
          <AssistantEditors assistant={assistant} />
          <ActionApprovers assistant={assistant} />
          {admin && <OfficialToggle assistant={assistant} onChanged={onChanged} />}
          {rows.length === 0 && !error && (
            <EmptyState
              className="mt-4"
              title="Nothing submitted yet"
              description="Submit the draft for review to create version 1. Each version keeps the assistant as it was, so you can compare them here."
            />
          )}
          {rows.length > 0 && (
            <ul className="border-border mt-4 border-t">
              {rows.map((v) => (
                <li key={v.id} className="border-border flex items-start gap-3 border-b py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="num text-sm font-medium">Version {v.version_number}</p>
                      <Badge
                        variant={
                          v.id === assistant.current_version_id ? "success" : STATUS_TONE[v.status]
                        }
                      >
                        {v.id === assistant.current_version_id
                          ? "Live"
                          : VERSION_STATUS_WORD[v.status]}
                      </Badge>
                    </div>
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
                    {v.status === "submitted" && admin && ruleFor(v) === "refused" && (
                      <p className="text-small text-muted-foreground">
                        You submitted this or built the assistant, so another admin or the owner
                        approves it.
                      </p>
                    )}
                    {v.review_note && (
                      <p className="text-small text-muted-foreground" title={v.review_note}>
                        Review: {v.review_note}
                      </p>
                    )}
                    {v.status === "submitted" &&
                      liveNumber !== undefined &&
                      liveNumber !== v.version_number && (
                        <Button
                          type="button"
                          variant="link"
                          size="sm"
                          className="mt-1"
                          onClick={() => {
                            setFrom(liveNumber);
                            setTo(v.version_number);
                          }}
                        >
                          Compare with the live version
                        </Button>
                      )}
                    <VersionActions
                      assistantId={assistantId}
                      number={v.version_number}
                      actions={versionActions(v, {
                        live: v.id === assistant.current_version_id,
                        admin,
                        editor,
                        approve: ruleFor(v),
                      })}
                      busy={acting}
                      onAction={(a) => void onAction(a, v.version_number)}
                    />
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
          {/* Approve on evidence (Phase 10.4): for the people who review. */}
          {admin &&
            to !== null &&
            rows.some((r) => r.version_number === to && r.status === "submitted") && (
              <ApprovalEvidence assistantId={assistantId} number={to} />
            )}
          {to !== null && <VersionListingSummary assistantId={assistantId} number={to} />}
          {rows.length === 1 && (
            <p className="text-muted-foreground max-w-[60ch] text-sm">
              Only one version so far. Submit again to have something to compare.
            </p>
          )}
          {from !== null && from === to && (
            <p className="text-muted-foreground text-sm">
              Pick two different versions to see what changed.
            </p>
          )}
          {drift && drift.items.length > 0 && (
            <DriftNotice
              number={drift.number}
              live={drift.number === liveNumber}
              items={drift.items}
            />
          )}
          {manifests && <UsesChanges from={from} to={to} manifests={manifests} />}
          {diff && <DiffView diff={diff} graphs={graphs} sourceLabels={sourceLabels} />}
        </section>
      </div>
    </div>
  );
}

const ACTION_LABEL: Record<
  Exclude<ReturnType<typeof versionActions>[number], "preview">,
  string
> = {
  approve: "Approve",
  reject: "Reject",
  withdraw: "Withdraw",
  "make-live": "Roll back to this",
};

/** What can be done with one version: review it, take it back, roll back
 *  to it, or chat with it in a preview. */
function VersionActions({
  assistantId,
  number,
  actions,
  busy,
  onAction,
}: {
  assistantId: string;
  number: number;
  actions: ReturnType<typeof versionActions>;
  busy: boolean;
  onAction: (action: ReturnType<typeof versionActions>[number]) => void;
}) {
  if (actions.length === 0) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {actions.map((a) =>
        a === "preview" ? (
          <Link
            key={a}
            href={previewHref(assistantId, number)}
            className={buttonVariants({ variant: "ghost", size: "sm" })}
            aria-label={`Preview version ${number}`}
          >
            Preview
          </Link>
        ) : (
          <Button
            key={a}
            type="button"
            size="sm"
            variant={a === "approve" ? "default" : "outline"}
            disabled={busy}
            onClick={() => onAction(a)}
            aria-label={`${ACTION_LABEL[a]}: version ${number}`}
          >
            {ACTION_LABEL[a]}
          </Button>
        ),
      )}
    </div>
  );
}

/** Something a version uses has changed since it was submitted. */
function DriftNotice({ number, live, items }: { number: number; live: boolean; items: string[] }) {
  return (
    <Alert className="mb-6" role="status">
      <p className="font-medium">Version {number} has changed since it was submitted.</p>
      <ul className="mt-1 list-disc pl-5">
        {items.map((t) => (
          <li key={t}>{t}.</li>
        ))}
      </ul>
      <p className="mt-1">
        {live
          ? "Chat leaves these out of its answers until a new version is approved."
          : "It can't be approved or rolled back to until it is submitted again."}
      </p>
    </Alert>
  );
}

/** What changed in what the two versions use: knowledge, connections and
 *  MCP tools, as each was submitted (Phase 7b.1). */
function UsesChanges({
  from,
  to,
  manifests,
}: {
  from: number | null;
  to: number | null;
  manifests: { from: Manifest | null; to: Manifest | null };
}) {
  if (!manifests.from || !manifests.to) return null;
  const changes = compareManifests(manifests.from, manifests.to);
  return (
    <section aria-labelledby="uses-heading" className="mb-8">
      <SectionHeading
        id="uses-heading"
        level={3}
        title="Knowledge, connections and tools"
        description={`What version ${to} uses, against version ${from}.`}
      />
      {changes.length === 0 ? (
        <p className="text-muted-foreground mt-2 text-sm">No change.</p>
      ) : (
        <ul className="border-border mt-2 border-t">
          {changes.map((c) => (
            <li key={c.text} className="border-border text-small border-b py-2">
              {c.text}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Where the assistant stands in Chat, and an admin's pause switch. */
function LiveState({
  status,
  liveNumber,
  admin,
  busy,
  onPause,
  onReopen,
}: {
  status: AssistantDetail["status"];
  liveNumber: number | undefined;
  admin: boolean;
  busy: boolean;
  onPause: () => void;
  onReopen: () => void;
}) {
  const text =
    status === "archived"
      ? "Paused by an admin. Nothing answers, previews included."
      : liveNumber !== undefined
        ? `Chat answers from version ${liveNumber}.`
        : "Nobody can chat with it until an admin approves a version.";
  return (
    <div className="border-border mt-3 flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2">
      <p className="text-small text-foreground min-w-0 flex-1">{text}</p>
      {admin &&
        (status === "archived" ? (
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={onReopen}>
            Reopen
          </Button>
        ) : (
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={onPause}>
            Pause
          </Button>
        ))}
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
