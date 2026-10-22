"use client";

import { useCallback, useEffect, useState } from "react";

import { RequestRow } from "@/components/approvals/RequestRow";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Loading } from "@/components/ui/loading";
import { PageHeader } from "@/components/ui/page-header";
import { ApiError, approvals, type DecidableApproval } from "@/lib/api";
import { failureMessage } from "@/lib/format";

/** My requests (Phase 9.2): everything you asked an assistant to do that
 *  needed someone's go-ahead, newest first, and where each stands. Each
 *  opens its page, with Withdraw while it waits and a link back to its
 *  conversation. */
export default function MyRequestsPage() {
  const [rows, setRows] = useState<DecidableApproval[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [more, setMore] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const page = await approvals.mine();
      setRows(page.items);
      setNext(page.next_cursor ?? null);
    } catch (err) {
      setError(
        failureMessage(
          "Couldn't load your requests.",
          err instanceof ApiError ? err.message : null,
          "Check your connection, then reload the page.",
        ),
      );
    }
  }, []);

  useEffect(() => {
    void load();
    // A request can be decided at any time: keep the list current.
    const timer = setInterval(() => void load(), 60_000);
    return () => clearInterval(timer);
  }, [load]);

  const loadMore = async () => {
    if (!next) return;
    setMore(true);
    try {
      const page = await approvals.mine(next);
      setRows((r) => [...(r ?? []), ...page.items]);
      setNext(page.next_cursor ?? null);
    } finally {
      setMore(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-200 px-4 py-8 md:px-6 lg:px-8">
      <PageHeader
        title="My requests"
        description="What you asked an assistant to do that needed a go-ahead: yours, or an approver's. You're emailed when an approver decides."
      />
      {error ? (
        <Alert>{error}</Alert>
      ) : rows === null ? (
        <Loading what="your requests" rows={3} />
      ) : rows.length === 0 ? (
        <p className="text-muted-foreground border-border border-t pt-3">
          No requests yet. When you ask an assistant to change something, it shows here.
        </p>
      ) : (
        <>
          <ul className="border-border border-t">
            {rows.map((r) => (
              <RequestRow key={r.id} request={r} mine showAsker={false} />
            ))}
          </ul>
          {next && (
            <Button
              variant="outline"
              className="mt-4"
              disabled={more}
              onClick={() => void loadMore()}
            >
              {more ? "Loading…" : "Show more"}
            </Button>
          )}
        </>
      )}
    </div>
  );
}
