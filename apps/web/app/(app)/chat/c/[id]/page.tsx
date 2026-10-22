"use client";

import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";

import { LoadFailed, loadFailure, type LoadFailure } from "@/components/load-state";
import { Loading } from "@/components/ui/loading";
import { ApiError, conversations } from "@/lib/api";
import { useAuth } from "@/lib/auth";

/** A conversation's stable link (Phase 8.6): `/chat/c/{id}`, for emails and
 *  anything else that names a conversation without knowing its assistant
 *  or organisation. It finds both, switches to the organisation if it
 *  isn't the active one, and opens the conversation. Signed out, the
 *  layout sends the person through sign-in and back here. */
export default function ConversationLinkPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { ready, orgs, activeOrgId, setActiveOrg } = useAuth();
  const router = useRouter();
  const [failure, setFailure] = useState<LoadFailure | null>(null);

  useEffect(() => {
    if (!ready) return;
    let gone = false;
    const order = [activeOrgId, ...orgs.map((o) => o.id).filter((o) => o !== activeOrgId)];
    void (async () => {
      for (const org of order) {
        if (!org) continue;
        try {
          const c = await conversations.get(id, org);
          if (gone) return;
          if (org !== activeOrgId) setActiveOrg(org);
          router.replace(`/assistants/${c.assistant_id}/chat?conversation=${id}`);
          return;
        } catch (err) {
          // Not in this organisation (the API says "not found" for that):
          // try the next. Anything else is the answer.
          if (err instanceof ApiError && err.status === 404) continue;
          if (!gone) setFailure(loadFailure(err));
          return;
        }
      }
      if (!gone) setFailure({ notFound: true, message: "", reached: true });
    })();
    return () => {
      gone = true;
    };
  }, [ready, id, orgs, activeOrgId, setActiveOrg, router]);

  if (failure) {
    return (
      <LoadFailed
        failure={failure}
        what="conversation"
        back={{ href: "/chat", label: "Back to Chat" }}
      />
    );
  }
  return (
    <div className="px-4 py-6 md:px-8">
      <Loading what="the conversation" />
    </div>
  );
}
