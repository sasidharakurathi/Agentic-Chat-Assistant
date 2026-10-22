"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { DecisionCard } from "@/components/approvals/DecisionCard";
import { RequestView } from "@/components/approvals/RequestView";
import { Alert } from "@/components/ui/alert";
import { Loading } from "@/components/ui/loading";
import { useToast } from "@/components/ui/toast";
import { ApiError, approvals, type DecidableApproval } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { isEmployee } from "@/lib/roles";

/** One request (Phase 9.2): where an email links, after sign-in. Whoever
 *  may decide it sees the decision card; anyone else allowed to see it (the
 *  person who asked, whoever decided it, admins) sees where it stands.
 *  Nothing is decided by opening a link. */
export default function RequestPage() {
  const { id } = useParams<{ id: string }>();
  const { user, activeRole } = useAuth();
  const toast = useToast();
  const [request, setRequest] = useState<DecidableApproval | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setRequest(await approvals.one(id));
    } catch (err) {
      const gone = err instanceof ApiError && (err.status === 404 || err.status === 403);
      setError(
        gone
          ? "This request isn't one you can see: it may belong to another organisation, or to someone else."
          : "Couldn't load this request. Check your connection, then reload the page.",
      );
    }
  }, [id]);

  useEffect(() => void load(), [load]);

  const back =
    isEmployee(activeRole) && request?.requested_by === user?.id
      ? { href: "/chat/requests", label: "My requests" }
      : { href: "/approvals", label: "Approvals" };

  return (
    <div className="mx-auto w-full max-w-200 px-4 py-8 md:px-6 lg:px-8">
      <Link
        href={back.href}
        className="text-small text-muted-foreground hover:text-foreground mb-4 inline-block underline-offset-4 hover:underline"
      >
        ‹ {back.label}
      </Link>
      {error ? (
        <Alert>{error}</Alert>
      ) : request === null ? (
        <Loading what="the request" rows={4} />
      ) : request.can_decide ? (
        <DecisionCard
          request={request}
          onDecided={(message) => {
            toast(message);
            void load();
          }}
        />
      ) : (
        <RequestView request={request} me={user?.id} onChanged={() => void load()} />
      )}
    </div>
  );
}
