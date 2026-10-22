"use client";

import { useCallback, useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Loading } from "@/components/ui/loading";
import { PageHeader } from "@/components/ui/page-header";
import { ApiError, alerts as alertsApi, type AdminAlertItem } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatRelative } from "@/lib/format";

/** Alerts (Phase 8.9): what stopped Chat for everyone, when, how often, and
 *  whether the admins were emailed. An answer that works clears most of
 *  them; an admin can mark one handled. */
export default function AlertsPage() {
  const { activeOrgId } = useAuth();
  const [rows, setRows] = useState<AdminAlertItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!activeOrgId) return;
    try {
      setRows(await alertsApi.list(activeOrgId, false));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Couldn't reach the server. Check your connection, then reload.",
      );
    }
  }, [activeOrgId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handled = async (a: AdminAlertItem) => {
    if (!activeOrgId) return;
    try {
      await alertsApi.resolve(activeOrgId, a.kind, a.subject);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't mark it handled. Try again.");
    }
  };

  return (
    <div className="mx-auto w-full max-w-240 px-4 py-8 md:px-6 lg:px-8">
      <PageHeader
        title="Alerts"
        description="What stopped Chat for everyone. Admins and the owner are emailed at most once an hour for each problem; the next answer that works clears it, or mark it handled here."
      />
      {error && <Alert className="mb-6">{error}</Alert>}
      {rows === null ? (
        !error && <Loading what="alerts" />
      ) : rows.length === 0 ? (
        <EmptyState
          title="Nothing has gone wrong"
          description="Problems that stop Chat for everyone appear here."
        />
      ) : (
        <ul className="border-border flex flex-col border-t">
          {rows.map((a) => (
            <li
              key={`${a.kind}-${a.subject}-${a.first_at}`}
              className="border-border flex flex-wrap items-start gap-3 border-b py-3"
            >
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-2 font-medium">
                  {a.what}
                  {a.subject_name ? `: ${a.subject_name}` : ""}
                  {a.resolved_at ? (
                    <Badge variant="muted">Resolved</Badge>
                  ) : (
                    <Badge variant="destructive">Open</Badge>
                  )}
                </p>
                <p className="text-small text-muted-foreground">
                  {a.count === 1 ? "Once" : `${a.count} times`}, last {formatRelative(a.last_at)}
                  {a.emailed_at ? `; emailed ${formatRelative(a.emailed_at)}` : "; not emailed yet"}
                  {a.resolved_at ? `; resolved ${formatRelative(a.resolved_at)}` : ""}
                </p>
                {a.detail && <p className="text-small mt-1 break-words">{a.detail}</p>}
              </div>
              {!a.resolved_at && (
                <Button size="sm" variant="outline" onClick={() => void handled(a)}>
                  Mark handled
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
