"use client";

import { useEffect, useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { fieldClasses } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SectionHeading } from "@/components/ui/section-heading";
import { ApiError, orgs, type NoticeAdmin, type NoticeRevision } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The longest notice: matches the API's limit. */
const BODY_MAX = 8000;

/** The privacy notice (Phase 8.7): what everyone reads and accepts before
 *  their first message in Chat. Admins see it and how many accepted; the
 *  owner edits it, and chooses whether a change asks everyone again (a real
 *  change) or not (a typo). Every saved text is kept. */
export function NoticeSettings({ orgId }: { orgId: string }) {
  const [notice, setNotice] = useState<NoticeAdmin | null>(null);
  const [draft, setDraft] = useState("");
  const [askAgain, setAskAgain] = useState(true);
  const [revisions, setRevisions] = useState<NoticeRevision[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const id = useId();

  useEffect(() => {
    orgs
      .notice(orgId)
      .then((n) => {
        setNotice(n);
        setDraft(n.body);
      })
      .catch(() => setNotice(null));
  }, [orgId]);

  if (!notice) return null;
  const changed = draft.trim() !== notice.body;

  const save = async () => {
    setBusy(true);
    setNote(null);
    try {
      const next = await orgs.saveNotice(orgId, draft, askAgain);
      setNotice(next);
      setDraft(next.body);
      setRevisions(null);
      setNote({
        ok: true,
        text: askAgain
          ? `Saved as version ${next.version}: everyone accepts it before their next message.`
          : `Saved. Still version ${next.version}: nobody is asked again.`,
      });
    } catch (err) {
      setNote({
        ok: false,
        text: `Couldn't save the notice. ${err instanceof ApiError ? err.message : "Try again."}`,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby={`${id}-heading`} className="flex max-w-160 flex-col gap-4">
      <SectionHeading
        level={2}
        id={`${id}-heading`}
        title="How Chat uses your data"
        description={`Everyone reads and accepts this before their first message in Chat. Version ${notice.version}, accepted by ${notice.accepted} ${notice.accepted === 1 ? "person" : "people"}${notice.custom ? "" : "; still the built-in text"}.`}
      />
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-body`}>The notice</Label>
        <p id={`${id}-hint`} className="text-small text-muted-foreground">
          Plain text. A blank line starts a paragraph, &quot;- &quot; starts a list item, and a
          short line just above a list is its heading. Where messages go is added below it from the
          server&apos;s setup, with your privacy contact (organisation settings).
        </p>
        <textarea
          id={`${id}-body`}
          aria-describedby={`${id}-hint`}
          rows={16}
          maxLength={BODY_MAX}
          disabled={!notice.can_edit}
          className={cn(fieldClasses, "min-h-64 resize-y px-3 py-2")}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
      </div>
      {notice.can_edit && (
        <>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-1"
              checked={askAgain}
              onChange={(e) => setAskAgain(e.target.checked)}
            />
            <span>
              Ask everyone to accept it again
              <span className="text-small text-muted-foreground block">
                For a real change. Leave it off to fix a typo.
              </span>
            </span>
          </label>
          <div className="flex flex-wrap gap-2">
            <Button disabled={busy || !changed || !draft.trim()} onClick={() => void save()}>
              {busy ? "Saving…" : "Save the notice"}
            </Button>
            {draft.trim() !== notice.default_body && (
              <Button variant="ghost" onClick={() => setDraft(notice.default_body)}>
                Start from the built-in text
              </Button>
            )}
          </div>
        </>
      )}
      {note && <Alert tone={note.ok ? "success" : undefined}>{note.text}</Alert>}
      {notice.custom && (
        <details
          onToggle={(e) => {
            if ((e.target as HTMLDetailsElement).open && revisions === null) {
              orgs
                .noticeRevisions(orgId)
                .then(setRevisions)
                .catch(() => setRevisions([]));
            }
          }}
        >
          <summary className="cursor-pointer font-medium">Earlier texts</summary>
          <ul className="mt-2 flex flex-col gap-3">
            {(revisions ?? []).map((r) => (
              <li key={r.revision}>
                <p className="text-small text-muted-foreground">
                  Version {r.version}, saved {formatRelative(r.created_at)}
                  {r.created_by ? ` by ${r.created_by}` : ""}
                </p>
                <pre className="bg-muted border-border mt-1 max-h-48 overflow-auto rounded-md border p-3 text-sm break-words whitespace-pre-wrap">
                  {r.body}
                </pre>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
