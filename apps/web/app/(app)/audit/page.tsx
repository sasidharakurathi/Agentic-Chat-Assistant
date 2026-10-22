"use client";

import { useCallback, useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Loading } from "@/components/ui/loading";
import { PageHeader } from "@/components/ui/page-header";
import { Select } from "@/components/ui/select";
import {
  ApiError,
  orgs,
  type AuditEntry,
  type AuditFilters,
  type AuditIntegrity,
  type Member,
} from "@/lib/api";
import { AUDIT_KINDS, auditDetail, describeAction } from "@/lib/audit-words";
import { useAuth } from "@/lib/auth";
import { failureMessage, formatRelative } from "@/lib/format";

const PERIODS: { value: string; label: string; days: number | null }[] = [
  { value: "all", label: "Any time", days: null },
  { value: "1", label: "Last 24 hours", days: 1 },
  { value: "7", label: "Last 7 days", days: 7 },
  { value: "30", label: "Last 30 days", days: 30 },
];

function sinceOf(period: string): string | undefined {
  const days = PERIODS.find((p) => p.value === period)?.days;
  return days ? new Date(Date.now() - days * 86_400_000).toISOString() : undefined;
}

/** Who did what in this organization (Phase 7b.7). Admins read it; the
 *  owner can also export it and check that nothing in it was changed. */
export default function AuditPage() {
  const { activeOrgId, activeRole } = useAuth();
  const owner = activeRole === "owner";
  const [kind, setKind] = useState("");
  const [actor, setActor] = useState("");
  const [period, setPeriod] = useState("30");
  const [people, setPeople] = useState<Member[]>([]);
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [more, setMore] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [checking, setChecking] = useState(false);
  const [integrity, setIntegrity] = useState<AuditIntegrity | null>(null);

  const filters = useCallback(
    (): AuditFilters => ({ action: kind, actor, since: sinceOf(period) }),
    [kind, actor, period],
  );

  const fail = (what: string, err: unknown) =>
    setError(
      failureMessage(
        what,
        err instanceof ApiError ? err.message : null,
        "Check your connection, then try again.",
      ),
    );

  const load = useCallback(async () => {
    if (!activeOrgId) return;
    setError(null);
    try {
      const page = await orgs.audit(activeOrgId, filters());
      setRows(page.items);
      setMore(page.next_cursor);
      setLoaded(true);
    } catch (err) {
      fail("Couldn't load the audit log.", err);
    }
  }, [activeOrgId, filters]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!activeOrgId) return;
    // The person filter is a convenience: without the list, it offers "Anyone".
    orgs
      .members(activeOrgId)
      .then((p) => setPeople(p.items))
      .catch(() => setPeople([]));
  }, [activeOrgId]);

  const loadMore = async () => {
    if (!activeOrgId || !more) return;
    setLoadingMore(true);
    try {
      const page = await orgs.audit(activeOrgId, filters(), more);
      setRows((r) => [...r, ...page.items]);
      setMore(page.next_cursor);
    } catch (err) {
      fail("Couldn't load more of the audit log.", err);
    } finally {
      setLoadingMore(false);
    }
  };

  const exportCsv = async () => {
    if (!activeOrgId) return;
    setExporting(true);
    try {
      const blob = await orgs.auditCsv(activeOrgId, filters());
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      // The export is itself recorded: show it.
      void load();
    } catch (err) {
      fail("Couldn't export the audit log.", err);
    } finally {
      setExporting(false);
    }
  };

  const check = async () => {
    if (!activeOrgId) return;
    setChecking(true);
    setIntegrity(null);
    try {
      setIntegrity(await orgs.auditIntegrity(activeOrgId));
    } catch (err) {
      fail("Couldn't check the audit log.", err);
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-240 px-4 py-8 md:px-6 lg:px-8">
      <PageHeader
        title="Audit log"
        description="Who did what in this organization, newest first. Entries can't be edited or deleted, by anyone."
        actions={
          owner && (
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" disabled={checking} onClick={() => void check()}>
                {checking ? "Checking…" : "Check integrity"}
              </Button>
              <Button variant="outline" disabled={exporting} onClick={() => void exportCsv()}>
                {exporting ? "Exporting…" : "Export CSV"}
              </Button>
            </div>
          )
        }
      />

      {integrity && <IntegrityResult found={integrity} />}
      {error && <Alert className="mb-6">{error}</Alert>}

      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="audit-kind">What</Label>
          <Select id="audit-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            {AUDIT_KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="audit-actor">Who</Label>
          <Select id="audit-actor" value={actor} onChange={(e) => setActor(e.target.value)}>
            <option value="">Anyone</option>
            {people.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.name ? `${m.name} (${m.email})` : m.email}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="audit-period">When</Label>
          <Select id="audit-period" value={period} onChange={(e) => setPeriod(e.target.value)}>
            {PERIODS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {!loaded && !error ? (
        <Loading what="the audit log" rows={6} />
      ) : (
        loaded && (
          <section aria-label="Entries" className="flex flex-col gap-3">
            {rows.length === 0 ? (
              <p className="text-muted-foreground border-border border-t pt-3">
                Nothing recorded that matches.
              </p>
            ) : (
              <ul className="border-border border-t">
                {rows.map((r) => (
                  <AuditRow key={r.id} entry={r} />
                ))}
              </ul>
            )}
            {more && (
              <Button
                variant="ghost"
                size="sm"
                className="self-start"
                disabled={loadingMore}
                onClick={() => void loadMore()}
              >
                {loadingMore ? "Loading…" : "Load more"}
              </Button>
            )}
          </section>
        )
      )}
    </div>
  );
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const detail = auditDetail(entry.action, entry.meta as Record<string, unknown>);
  const who = entry.actor_label ?? (entry.actor_user_id ? "A deleted account" : "The system");
  return (
    <li className="border-border flex flex-wrap items-baseline gap-x-4 gap-y-0.5 border-b py-2.5">
      <div className="min-w-0 flex-[1_1_20rem]">
        <div>
          <span className="font-medium">{who}</span>: {describeAction(entry.action)}
        </div>
        {detail && <div className="text-small text-muted-foreground break-words">{detail}</div>}
      </div>
      <time
        dateTime={entry.created_at}
        title={new Date(entry.created_at).toLocaleString()}
        className="text-small text-muted-foreground num shrink-0"
      >
        {formatRelative(entry.created_at)}
      </time>
    </li>
  );
}

function IntegrityResult({ found }: { found: AuditIntegrity }) {
  if (!found.intact) {
    return (
      <Alert className="mb-6">
        {`The audit log has been changed outside the app: entry ${found.broken_at} no longer matches the chain. Keep the database as it is and tell whoever runs the server.`}
      </Alert>
    );
  }
  return (
    <div className="border-border mb-6 rounded-md border p-4" role="status">
      <p className="font-medium">
        Intact: {found.sealed} {found.sealed === 1 ? "entry checks" : "entries check"} out, from the
        first to the newest.
      </p>
      {found.last_hash && (
        <p className="text-small text-muted-foreground mt-1">
          Newest fingerprint, worth keeping somewhere else to prove later that nothing before it
          changed:{" "}
          <span className="bg-muted rounded-sm px-1 break-all select-all">{found.last_hash}</span>
        </p>
      )}
    </div>
  );
}
