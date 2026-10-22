"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { NoticeText } from "@/components/privacy/NoticeText";
import { Alert } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Loading } from "@/components/ui/loading";
import { BackLink, PageHeader } from "@/components/ui/page-header";
import { SectionHeading } from "@/components/ui/section-heading";
import { ApiError, chat, type ConversationRead, type Notice } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { contactLink } from "@/lib/notice-text";
import { cn } from "@/lib/utils";

/** Privacy & help (Phase 8.7): how Chat uses your data, who to ask, asking
 *  for your data, and each time an admin opened one of your conversations
 *  (with the reason they gave). */
export default function PrivacyPage() {
  const [notice, setNotice] = useState<Notice | null>(null);
  const [reads, setReads] = useState<ConversationRead[] | null>(null);
  const [more, setMore] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fail = (err: unknown) =>
    setError(
      err instanceof ApiError
        ? err.message
        : "Couldn't reach the server. Check your connection, then reload the page.",
    );

  useEffect(() => {
    chat.notice().then(setNotice).catch(fail);
    chat
      .access()
      .then((p) => {
        setReads(p.items);
        setMore(p.next_cursor);
      })
      .catch(fail);
  }, []);

  const loadMore = useCallback(async () => {
    if (!more) return;
    try {
      const p = await chat.access(more);
      setReads((r) => [...(r ?? []), ...p.items]);
      setMore(p.next_cursor);
    } catch (err) {
      fail(err);
    }
  }, [more]);

  const ask = notice ? contactLink(notice.contact, "A request about my data in Chat") : null;

  return (
    <div className="mx-auto w-full max-w-240 px-4 py-8 md:px-6 lg:px-8">
      <BackLink href="/chat" label="Chat" className="mb-4" />
      <PageHeader
        title="Privacy & help"
        description="How Chat uses your data, who to ask about it, and when an admin opened one of your conversations."
      />
      {error && <Alert className="mb-6">{error}</Alert>}
      {!notice ? (
        !error && <Loading what="the notice" />
      ) : (
        <div className="flex flex-col gap-10">
          <section aria-labelledby="notice-heading" className="flex flex-col gap-3">
            <SectionHeading
              level={2}
              id="notice-heading"
              title="How Chat uses your data"
              description={
                notice.accepted_at
                  ? `Version ${notice.version}. You accepted it ${formatRelative(notice.accepted_at)}.`
                  : `Version ${notice.version}.`
              }
            />
            <NoticeText notice={notice} />
          </section>

          <section aria-labelledby="request-heading" className="flex flex-col gap-3">
            <SectionHeading
              level={2}
              id="request-heading"
              title="Your data"
              description="To ask what is kept about you, or to have something corrected or removed, write to your company's privacy contact."
            />
            {ask ? (
              <a href={ask} className={cn(buttonVariants({ variant: "outline" }), "self-start")}>
                Write to {notice.contact}
              </a>
            ) : (
              <p className="text-muted-foreground">
                Your company hasn&apos;t named a privacy contact yet. Ask an admin.
              </p>
            )}
          </section>

          <section aria-labelledby="reads-heading" className="flex flex-col gap-3">
            <SectionHeading
              level={2}
              id="reads-heading"
              title="Access to your conversations"
              description="Each time an admin or the owner opened one of your conversations, with the reason they had to give. People who run the server and read its database directly aren't recorded here."
            />
            {reads === null ? (
              <Loading what="the record" />
            ) : reads.length === 0 ? (
              <p className="text-muted-foreground">Nobody has opened any of your conversations.</p>
            ) : (
              <ul className="border-border flex flex-col border-t">
                {reads.map((r, i) => (
                  <li key={`${r.read_at}-${i}`} className="border-border border-b py-3">
                    <p>
                      <span className="font-medium">{r.reader}</span>
                      {r.reader_role ? ` (${r.reader_role})` : ""} opened{" "}
                      {r.conversation_id ? (
                        <Link
                          href={`/chat/c/${r.conversation_id}`}
                          className="font-medium underline decoration-1 underline-offset-[3px] hover:decoration-2"
                        >
                          {r.conversation_title}
                        </Link>
                      ) : (
                        r.conversation_title
                      )}{" "}
                      {formatRelative(r.read_at)}.
                    </p>
                    <p className="text-muted-foreground text-small">
                      Reason: {r.reason || "none given"}
                    </p>
                  </li>
                ))}
              </ul>
            )}
            {more && (
              <Button variant="ghost" className="self-start" onClick={() => void loadMore()}>
                Show earlier
              </Button>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
