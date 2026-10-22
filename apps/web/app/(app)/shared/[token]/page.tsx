"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";

import { Markdown } from "@/components/chat/Markdown";
import { SourcesPanel } from "@/components/chat/SourcesPanel";
import { LoadFailed, loadFailure, type LoadFailure } from "@/components/load-state";
import { buttonVariants } from "@/components/ui/button";
import { Loading } from "@/components/ui/loading";
import { shares, type SharedConversation } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { splitBlocks } from "@/lib/message-blocks";

/** A conversation someone shared (Phase 9.4): read-only, and only for
 *  people who may use its assistant and read the knowledge it cites. */
export default function SharedConversationPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const [shared, setShared] = useState<SharedConversation | null>(null);
  const [failure, setFailure] = useState<LoadFailure | null>(null);

  useEffect(() => {
    shares
      .open(token)
      .then(setShared)
      .catch((err) => setFailure(loadFailure(err)));
  }, [token]);

  const back = { href: "/chat", label: "Back to Chat" };
  if (failure) return <LoadFailed failure={failure} what="shared conversation" back={back} />;
  if (!shared) {
    return (
      <div className="px-4 py-6 md:px-8">
        <Loading what="conversation" />
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-[72ch] flex-col gap-6 px-4 py-6 md:px-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-h2 font-semibold">{shared.title ?? "Conversation"}</h1>
        <p className="text-small text-muted-foreground">
          With {shared.assistant_name}, shared by {shared.shared_by}{" "}
          {formatRelative(shared.shared_at)}. Read-only.
        </p>
      </header>
      {shared.messages.map((m) => {
        if (m.role === "user") {
          return (
            <p
              key={m.id}
              className="bg-muted self-end rounded-lg px-4 py-2 whitespace-pre-wrap"
              aria-label="Question"
            >
              {m.content}
            </p>
          );
        }
        const cites = splitBlocks(m.blocks).cites;
        return (
          <article key={m.id} className="flex min-w-0 flex-col gap-4">
            <h2 className="sr-only">{shared.assistant_name}</h2>
            <Markdown text={m.content} citations={cites} />
            {cites.length > 0 && (
              <SourcesPanel citations={cites} assistantId={shared.assistant_id} />
            )}
          </article>
        );
      })}
      <Link
        href={`/assistants/${shared.assistant_id}/chat`}
        className={buttonVariants({ variant: "outline", className: "self-start" })}
      >
        Ask {shared.assistant_name} yourself
      </Link>
    </div>
  );
}
