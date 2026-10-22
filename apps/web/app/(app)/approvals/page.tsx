"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useState } from "react";

import { DecisionCard } from "@/components/approvals/DecisionCard";
import { RequestRow } from "@/components/approvals/RequestRow";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Loading } from "@/components/ui/loading";
import { PageHeader } from "@/components/ui/page-header";
import { Select } from "@/components/ui/select";
import { Tabs, tabPanelProps, type TabOption } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { ApiError, approvals, assistants, type Assistant, type DecidableApproval } from "@/lib/api";
import { isAdminRole } from "@/lib/assistant-lifecycle";
import { useAuth } from "@/lib/auth";
import { failureMessage } from "@/lib/format";

type View = "waiting" | "decided" | "all";

const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: "", label: "Any state" },
  { value: "pending", label: "Waiting" },
  { value: "succeeded", label: "Done" },
  { value: "failed", label: "Didn't go through" },
  { value: "denied", label: "Declined" },
  { value: "expired", label: "Not decided in time" },
  { value: "cancelled", label: "Withdrawn" },
];

const TABS_ID = "approvals";

/** Requests (Phase 9.1, 9.2): what waits for you to decide, with exactly
 *  what would run so it is decided here; what you decided; and, for
 *  admins, every request in the organisation. Each opens its own page,
 *  where emails link. The conversations stay private. */
export default function ApprovalsPage() {
  const toast = useToast();
  const { activeRole, activeOrgId } = useAuth();
  const admin = isAdminRole(activeRole);
  const [view, setView] = useState<View>("waiting");
  const [waiting, setWaiting] = useState<DecidableApproval[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadWaiting = useCallback(async () => {
    setError(null);
    try {
      setWaiting(await approvals.decidable());
    } catch (err) {
      setError(
        failureMessage(
          "Couldn't load the requests waiting.",
          err instanceof ApiError ? err.message : null,
          "Check your connection, then reload the page.",
        ),
      );
    }
  }, []);

  useEffect(() => {
    void loadWaiting();
    // Someone else may decide one: keep the list current while it is open.
    const timer = setInterval(() => void loadWaiting(), 30_000);
    return () => clearInterval(timer);
  }, [loadWaiting]);

  const tabs: TabOption<View>[] = [
    { value: "waiting", label: waiting?.length ? `Waiting (${waiting.length})` : "Waiting" },
    { value: "decided", label: "Decided by you" },
    ...(admin ? [{ value: "all" as const, label: "All requests" }] : []),
  ];

  return (
    <div className="mx-auto w-full max-w-240 px-4 py-8 md:px-6 lg:px-8">
      <PageHeader
        title="Approvals"
        description="Requests that wait for someone to decide. Nothing runs until someone does; after a few days unanswered, a request expires and the person who asked is told."
      />
      <p className="text-small text-muted-foreground mb-4">
        Away for a while?{" "}
        <Link href="/account#away" className="text-foreground underline underline-offset-4">
          Hand your requests to someone
        </Link>
        .
      </p>
      <Tabs
        label="Requests"
        idBase={TABS_ID}
        value={view}
        onChange={setView}
        options={tabs}
        className="border-border mb-6 border-b"
      />
      <div {...tabPanelProps(TABS_ID, view)} className="focus-visible:outline-none">
        {view === "waiting" &&
          (error ? (
            <Alert>{error}</Alert>
          ) : waiting === null ? (
            <Loading what="requests" rows={3} />
          ) : waiting.length === 0 ? (
            <p className="text-muted-foreground">Nothing waits for you.</p>
          ) : (
            <ul className="flex flex-col gap-4">
              {waiting.map((r) => (
                <li key={`${r.id}:${r.input_sha256 ?? ""}`}>
                  <DecisionCard
                    request={r}
                    onDecided={(message) => {
                      toast(message);
                      void loadWaiting();
                    }}
                  />
                </li>
              ))}
            </ul>
          ))}
        {view === "decided" && <DecidedList />}
        {view === "all" && admin && activeOrgId && <OrgList orgId={activeOrgId} />}
      </div>
    </div>
  );
}

/** A paged list of requests, newest first. */
function usePaged(
  load: (cursor?: string | null) => Promise<{
    items: DecidableApproval[];
    next_cursor?: string | null;
  }>,
) {
  const [rows, setRows] = useState<DecidableApproval[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [more, setMore] = useState(false);

  const first = useCallback(async () => {
    setError(null);
    setRows(null);
    try {
      const page = await load();
      setRows(page.items);
      setNext(page.next_cursor ?? null);
    } catch (err) {
      setError(
        failureMessage(
          "Couldn't load the requests.",
          err instanceof ApiError ? err.message : null,
          "Check your connection, then reload the page.",
        ),
      );
    }
  }, [load]);

  const loadMore = async () => {
    if (!next) return;
    setMore(true);
    try {
      const page = await load(next);
      setRows((r) => [...(r ?? []), ...page.items]);
      setNext(page.next_cursor ?? null);
    } finally {
      setMore(false);
    }
  };

  useEffect(() => void first(), [first]);
  return { rows, next, error, more, loadMore };
}

function PagedList({ state, empty }: { state: ReturnType<typeof usePaged>; empty: string }) {
  const { rows, next, error, more, loadMore } = state;
  if (error) return <Alert>{error}</Alert>;
  if (rows === null) return <Loading what="requests" rows={3} />;
  if (rows.length === 0) return <p className="text-muted-foreground">{empty}</p>;
  return (
    <>
      <ul className="border-border border-t">
        {rows.map((r) => (
          <RequestRow key={r.id} request={r} />
        ))}
      </ul>
      {next && (
        <Button variant="outline" className="mt-4" disabled={more} onClick={() => void loadMore()}>
          {more ? "Loading…" : "Show more"}
        </Button>
      )}
    </>
  );
}

function DecidedList() {
  const load = useCallback((cursor?: string | null) => approvals.decided(cursor), []);
  return <PagedList state={usePaged(load)} empty="You haven't decided any requests yet." />;
}

/** Every request in the organisation, by state and by assistant. */
function OrgList({ orgId }: { orgId: string }) {
  const id = useId();
  const [status, setStatus] = useState("");
  const [assistantId, setAssistantId] = useState("");
  const [options, setOptions] = useState<Assistant[]>([]);
  useEffect(() => {
    assistants
      .list()
      .then((page) => setOptions(page.items))
      .catch(() => setOptions([]));
  }, []);
  const load = useCallback(
    (cursor?: string | null) =>
      approvals.inOrg(orgId, { status, assistant_id: assistantId }, cursor),
    [orgId, status, assistantId],
  );
  const state = usePaged(load);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor={`${id}-status`}>State</Label>
          <Select id={`${id}-status`} value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUS_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={`${id}-assistant`}>Assistant</Label>
          <Select
            id={`${id}-assistant`}
            value={assistantId}
            onChange={(e) => setAssistantId(e.target.value)}
          >
            <option value="">Every assistant</option>
            {options.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </Select>
        </div>
      </div>
      <PagedList state={state} empty="No requests match." />
    </div>
  );
}
