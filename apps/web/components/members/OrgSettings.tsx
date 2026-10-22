"use client";

import { useEffect, useId, useState } from "react";

import { InternalDestinations } from "@/components/members/InternalDestinations";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { fieldClasses, Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SectionHeading } from "@/components/ui/section-heading";
import { Switch } from "@/components/ui/switch";
import { ApiError, orgs, type OrgSettings as Settings } from "@/lib/api";
import { parseLimit } from "@/lib/budgets";
import { cn } from "@/lib/utils";

/** The Chat app's offline steps (Phase 8.6): matches the API's limit. */
const OFFLINE_HELP_MAX = 1000;

type SwitchKey =
  | "review_emails"
  | "decision_emails"
  | "approval_emails"
  | "google_auto_join"
  | "sole_reviewer_self_approval";

const SWITCHES: { key: SwitchKey; label: string; hint: string }[] = [
  {
    key: "review_emails",
    label: "Email admins when a version waits for review",
    hint: "Admins and the owner, except whoever submitted it.",
  },
  {
    key: "decision_emails",
    label: "Email builders when their version is decided",
    hint: "Approved or rejected, with a link to the note.",
  },
  {
    key: "approval_emails",
    label: "Email approvers when a request waits for them",
    hint: "The assistant's approvers (admins when it names nobody), and a reminder once if it still waits.",
  },
  {
    key: "google_auto_join",
    label: "Let the company's Google accounts join without an invite",
    hint: "They join as Employees, checked on Google's own record of the account's domain. Needs Google sign-in set up on the server with its domain.",
  },
  {
    key: "sole_reviewer_self_approval",
    label: "Let the only reviewer approve their own work",
    hint: "When nobody else can review, the owner may approve their own version with a note. Turn off once there is another admin.",
  },
];

/** The builder budget as the field shows it (Phase 9.7): empty is none. */
const capText = (s: Settings) =>
  s.builder_monthly_usd == null ? "" : String(s.builder_monthly_usd);

/** The organisation's name and switches (Phase 7b.8). Admins see them;
 *  the owner changes them, and every change is in the audit log. */
export function OrgSettings({ orgId, onRenamed }: { orgId: string; onRenamed: () => void }) {
  const [saved, setSaved] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Settings | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  // The builder budget as typed (Phase 9.7): empty means no limit.
  const [builderCap, setBuilderCap] = useState("");
  const nameId = useId();

  useEffect(() => {
    orgs
      .settings(orgId)
      .then((s) => {
        setSaved(s);
        setDraft(s);
        setBuilderCap(capText(s));
      })
      .catch(() => setSaved(null));
  }, [orgId]);

  if (!saved || !draft) return null;
  const editable = saved.can_edit;
  const cap = parseLimit(builderCap);
  const changed =
    cap !== (saved.builder_monthly_usd ?? null) ||
    draft.name.trim() !== saved.name ||
    (draft.offline_help ?? "").trim() !== (saved.offline_help ?? "") ||
    (draft.privacy_contact ?? "").trim().toLowerCase() !== (saved.privacy_contact ?? "") ||
    draft.action_expiry_hours !== saved.action_expiry_hours ||
    draft.action_reminder_hours !== saved.action_reminder_hours ||
    SWITCHES.some(({ key }) => draft[key] !== saved[key]) ||
    JSON.stringify(draft.internal_destinations) !== JSON.stringify(saved.internal_destinations);

  const save = async () => {
    setBusy(true);
    setNote(null);
    try {
      const next = await orgs.updateSettings(orgId, {
        name: draft.name.trim() || undefined,
        offline_help: (draft.offline_help ?? "").trim(),
        privacy_contact: (draft.privacy_contact ?? "").trim(),
        action_expiry_hours: draft.action_expiry_hours,
        action_reminder_hours: draft.action_reminder_hours,
        internal_destinations: draft.internal_destinations,
        builder_monthly_usd: cap ?? null,
        ...Object.fromEntries(SWITCHES.map(({ key }) => [key, draft[key]])),
      });
      if (next.name !== saved.name) onRenamed();
      setSaved(next);
      setDraft(next);
      setBuilderCap(capText(next));
      setNote({ ok: true, text: "Settings saved." });
    } catch (err) {
      setNote({
        ok: false,
        text: `Couldn't save the settings. ${err instanceof ApiError ? err.message : "Try again."}`,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="org-settings-heading" className="flex max-w-160 flex-col gap-4">
      <SectionHeading
        level={2}
        id="org-settings-heading"
        title="Organisation"
        description={
          editable ? "Changes are recorded in the audit log." : "Only the owner can change these."
        }
      />
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={nameId}>Name</Label>
        <Input
          id={nameId}
          value={draft.name}
          maxLength={200}
          disabled={!editable}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        />
      </div>
      <ul className="border-border flex flex-col border-t">
        {SWITCHES.map(({ key, label, hint }) => (
          <li key={key} className="border-border flex items-start gap-3 border-b py-3">
            <div className="min-w-0 flex-1">
              <p id={`${nameId}-${key}`}>{label}</p>
              <p id={`${nameId}-${key}-hint`} className="text-small text-muted-foreground">
                {hint}
              </p>
            </div>
            <Switch
              checked={draft[key]}
              disabled={!editable}
              aria-labelledby={`${nameId}-${key}`}
              aria-describedby={`${nameId}-${key}-hint`}
              onCheckedChange={(v) => setDraft({ ...draft, [key]: v })}
            />
          </li>
        ))}
      </ul>
      <fieldset className="flex flex-col gap-3">
        <legend className="mb-1">Requests that wait for an approver</legend>
        <p id={`${nameId}-waits-hint`} className="text-small text-muted-foreground">
          How long one waits before it expires (the person who asked is told), and when the
          approvers are reminded once. A reminder of 0 sends none.
        </p>
        <div className="flex flex-wrap gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${nameId}-expiry`}>Expires after (hours)</Label>
            <Input
              id={`${nameId}-expiry`}
              type="number"
              min={1}
              max={720}
              className="w-32"
              aria-describedby={`${nameId}-waits-hint`}
              disabled={!editable}
              value={draft.action_expiry_hours ?? 72}
              onChange={(e) =>
                setDraft({ ...draft, action_expiry_hours: Number(e.target.value) || 1 })
              }
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${nameId}-reminder`}>Remind after (hours)</Label>
            <Input
              id={`${nameId}-reminder`}
              type="number"
              min={0}
              max={719}
              className="w-32"
              aria-describedby={`${nameId}-waits-hint`}
              disabled={!editable}
              value={draft.action_reminder_hours ?? 24}
              onChange={(e) =>
                setDraft({ ...draft, action_reminder_hours: Number(e.target.value) || 0 })
              }
            />
          </div>
        </div>
      </fieldset>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${nameId}-builder`}>Builder testing: monthly limit (USD)</Label>
        <p
          id={`${nameId}-builder-hint`}
          className={cn(
            "text-small max-w-[60ch]",
            cap === undefined ? "text-destructive" : "text-muted-foreground",
          )}
        >
          {cap === undefined
            ? "Enter an amount of at least $0.01, or leave it empty for no limit."
            : "What previews and eval runs may spend in a month, across the organisation. It is kept apart from the budgets on the Usage page, so testing never stops Chat. Empty: no limit."}
        </p>
        <Input
          id={`${nameId}-builder`}
          inputMode="decimal"
          className="w-40"
          placeholder="No limit"
          aria-describedby={`${nameId}-builder-hint`}
          aria-invalid={cap === undefined || undefined}
          disabled={!editable}
          value={builderCap}
          onChange={(e) => setBuilderCap(e.target.value)}
        />
      </div>
      <InternalDestinations
        value={draft.internal_destinations ?? []}
        editable={editable}
        onChange={(internal_destinations) => setDraft({ ...draft, internal_destinations })}
      />
      <div className="flex max-w-100 flex-col gap-1.5">
        <Label htmlFor={`${nameId}-contact`}>Privacy contact</Label>
        <p id={`${nameId}-contact-hint`} className="text-small text-muted-foreground">
          Who people write to about their data, named in the privacy notice (Phase 8.7). Empty: the
          owner.
        </p>
        <Input
          id={`${nameId}-contact`}
          type="email"
          aria-describedby={`${nameId}-contact-hint`}
          maxLength={254}
          disabled={!editable}
          placeholder="privacy@company.example"
          value={draft.privacy_contact ?? ""}
          onChange={(e) => setDraft({ ...draft, privacy_contact: e.target.value })}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${nameId}-offline`}>When the app can&apos;t reach the server</Label>
        <p id={`${nameId}-offline-hint`} className="text-small text-muted-foreground">
          Your steps for getting on the office network or VPN, shown by the Chat app when it
          can&apos;t connect. It keeps them from the last visit, since your IT handbook sits behind
          the same VPN.
        </p>
        <textarea
          id={`${nameId}-offline`}
          aria-describedby={`${nameId}-offline-hint`}
          rows={4}
          maxLength={OFFLINE_HELP_MAX}
          disabled={!editable}
          placeholder="e.g. Open Tailscale and switch it on, then try again. Still stuck? Call the IT desk on 4400."
          className={cn(fieldClasses, "min-h-24 resize-y px-3 py-2")}
          value={draft.offline_help ?? ""}
          onChange={(e) => setDraft({ ...draft, offline_help: e.target.value })}
        />
      </div>
      {saved.org_mode === "single" && (
        <p className="text-small text-muted-foreground">
          This server has one organisation: nobody can create another, and people join by invite.
        </p>
      )}
      {note && <Alert tone={note.ok ? "success" : undefined}>{note.text}</Alert>}
      {editable && (
        <Button
          className="self-start"
          disabled={busy || !changed || !draft.name.trim() || cap === undefined}
          onClick={() => void save()}
        >
          {busy ? "Saving…" : "Save settings"}
        </Button>
      )}
    </section>
  );
}
