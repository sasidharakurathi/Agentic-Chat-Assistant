"use client";

import { Share2 } from "lucide-react";
import { useCallback, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ApiError, shares, type Share } from "@/lib/api";
import { formatRelative } from "@/lib/format";

/** Share a conversation as a read-only link (Phase 9.4). It opens for
 *  colleagues who may use the assistant and read the knowledge it cites,
 *  while you are still here. The link is shown once. */
export function ShareButton({ conversationId }: { conversationId: string }) {
  const [open, setOpen] = useState(false);
  const [links, setLinks] = useState<Share[] | null>(null);
  const [fresh, setFresh] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setLinks(await shares.list(conversationId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't load the links.");
    }
  }, [conversationId]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const openLinks = (links ?? []).filter((l) => !l.revoked_at);

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => {
          setOpen(true);
          setFresh(null);
          void load();
        }}
      >
        <Share2 aria-hidden className="size-4" />
        Share
      </Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Share this conversation"
        description="A read-only link for colleagues. It opens for people who can use this assistant and read the knowledge it cites, while you are still part of the organisation. Each opening is recorded."
        footer={
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Done
          </Button>
        }
      >
        <div className="flex flex-col gap-4">
          {fresh ? (
            <div className="flex flex-col gap-1.5">
              <p className="text-small text-muted-foreground">
                Copy it now: it isn&apos;t shown again.
              </p>
              <div className="flex gap-2">
                <Input readOnly value={fresh} onFocus={(e) => e.currentTarget.select()} />
                <Button
                  onClick={() =>
                    void navigator.clipboard?.writeText(fresh).then(() => setCopied(true))
                  }
                >
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
            </div>
          ) : (
            <Button
              className="self-start"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  const made = await shares.create(conversationId);
                  setFresh(made.url);
                  setCopied(false);
                })
              }
            >
              Create a link
            </Button>
          )}
          {error && <Alert role="alert">{error}</Alert>}
          {openLinks.length > 0 && (
            <section aria-label="Open links" className="flex flex-col gap-1">
              <p className="font-medium">Open links</p>
              <ul className="border-border border-t">
                {openLinks.map((l) => (
                  <li key={l.id} className="border-border flex items-center gap-2 border-b py-2">
                    <p className="text-small min-w-0 flex-1">
                      Made {formatRelative(l.created_at)}.{" "}
                      {l.open_count
                        ? `Opened ${l.open_count} time${l.open_count === 1 ? "" : "s"}, last ${formatRelative(l.last_opened_at ?? l.created_at)}.`
                        : "Not opened yet."}
                    </p>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => void act(() => shares.revoke(conversationId, l.id))}
                    >
                      Revoke
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </Dialog>
    </>
  );
}
