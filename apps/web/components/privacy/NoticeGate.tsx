"use client";

import { ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { NoticeText } from "@/components/privacy/NoticeText";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Loading } from "@/components/ui/loading";
import { ApiError, chat, type Notice } from "@/lib/api";

/** Said by a conversation whose message was refused for want of the notice
 *  (the owner asked everyone again while it was open): the gate checks. */
export const NOTICE_REQUIRED_EVENT = "as:notice-required";

/** How Chat uses your data, before the first message (Phase 8.7). Until the
 *  version in force is accepted, Chat's pages show the notice instead of
 *  themselves. The server refuses the message anyway (`notice_required`):
 *  this is what makes that a screen to read rather than an error. */
export function NoticeGate({ children }: { children: ReactNode }) {
  const [notice, setNotice] = useState<Notice | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    chat
      .notice()
      .then((n) => {
        setNotice(n);
        setFailed(false);
      })
      // Can't tell: let the page load; a message is refused by the server
      // if it is still needed, and that brings this back.
      .catch(() => setFailed(true));
  }, []);

  useEffect(() => {
    load();
    window.addEventListener(NOTICE_REQUIRED_EVENT, load);
    return () => window.removeEventListener(NOTICE_REQUIRED_EVENT, load);
  }, [load]);

  if (failed) return <>{children}</>;
  if (!notice) {
    return (
      <div className="px-4 py-6 md:px-8">
        <Loading what="Chat" />
      </div>
    );
  }
  if (!notice.required || notice.accepted_at) return <>{children}</>;

  const accept = async () => {
    setBusy(true);
    setError(null);
    try {
      setNotice(await chat.acceptNotice(notice.version));
    } catch (err) {
      if (err instanceof ApiError && err.code === "notice_changed") {
        setError("The notice changed while you were reading it. Here is the new one.");
        load();
      } else {
        setError(
          err instanceof ApiError
            ? err.message
            : "Couldn't reach the server. Check your connection, then try again.",
        );
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-[72ch] px-4 py-8 md:px-6">
      <section aria-labelledby="notice-heading" className="flex flex-col gap-6">
        <div className="flex items-start gap-3">
          <ShieldCheck aria-hidden className="text-muted-foreground mt-1 size-6 shrink-0" />
          <div>
            <h1 id="notice-heading" className="font-condensed text-h1 font-semibold">
              How Chat uses your data
            </h1>
            <p className="text-muted-foreground">
              {notice.version > 1
                ? "It has been updated. Please read it before you carry on."
                : "Before your first message."}
            </p>
          </div>
        </div>
        <NoticeText notice={notice} />
        {error && <Alert>{error}</Alert>}
        <div className="border-border flex flex-wrap items-center gap-x-4 gap-y-2 border-t pt-4">
          <Button size="lg" disabled={busy} onClick={() => void accept()}>
            {busy ? "Saving…" : "I've read it: continue"}
          </Button>
          <p className="text-small text-muted-foreground">
            Version {notice.version}. You can read it again any time under Privacy &amp; help.
          </p>
        </div>
      </section>
    </div>
  );
}
