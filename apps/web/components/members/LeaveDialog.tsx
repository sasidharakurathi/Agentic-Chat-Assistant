"use client";

import { useEffect, useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Loading } from "@/components/ui/loading";
import { Select } from "@/components/ui/select";
import { ApiError, orgs, type Member } from "@/lib/api";
import type { Schemas } from "@assistant-studio/shared";

type Leaving = Schemas["LeavingOut"];

/** Removing someone from the organisation (Phase 7b.4): what they leave
 *  behind, who takes over their assistants, then the confirmation. */
export function LeaveDialog({
  orgId,
  person,
  members,
  meId,
  onClose,
  onDone,
}: {
  orgId: string;
  person: Member | null;
  /** Everyone, to choose who takes over their assistants. */
  members: Member[];
  meId: string | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const [leaving, setLeaving] = useState<Leaving | null>(null);
  const [heir, setHeir] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const heirId = useId();

  useEffect(() => {
    setLeaving(null);
    setError(null);
    setHeir(meId ?? "");
    if (!person) return;
    orgs
      .leaving(orgId, person.user_id)
      .then(setLeaving)
      .catch((err) =>
        setError(
          `Couldn't load what they leave behind. ${err instanceof ApiError ? err.message : "Check your connection."}`,
        ),
      );
  }, [orgId, person, meId]);

  if (!person) return null;
  const who = person.name || person.email;
  const heirs = members.filter((m) => m.is_active !== false && m.user_id !== person.user_id);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await orgs.deactivate(orgId, person.user_id, heir || null);
      onDone();
    } catch (err) {
      setError(
        `Couldn't remove ${who}. ${err instanceof ApiError ? err.message : "Check your connection, then try again."}`,
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Remove ${who}?`}
      description="They lose access straight away and are signed out everywhere. Their account and history stay, and you can reactivate them later."
      dismissOnBackdrop={false}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={() => void confirm()} disabled={busy || !leaving}>
            {busy ? "Removing…" : "Remove"}
          </Button>
        </>
      }
    >
      {error && <Alert role="alert">{error}</Alert>}
      {!leaving && !error && <Loading what="what they leave behind" rows={2} />}
      {leaving && (
        <div className="flex flex-col gap-4 text-sm">
          {leaving.assistants.length > 0 ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={heirId}>
                Who takes over their {leaving.assistants.length === 1 ? "assistant" : "assistants"}
              </Label>
              <Select id={heirId} value={heir} onChange={(e) => setHeir(e.target.value)}>
                {heirs.map((m) => (
                  <option key={m.user_id} value={m.user_id}>
                    {m.user_id === meId ? `You (${m.name || m.email})` : m.name || m.email}
                  </option>
                ))}
              </Select>
              <p className="text-small text-muted-foreground">
                {leaving.assistants.map((a) => a.name).join(", ")}
              </p>
            </div>
          ) : (
            <p className="text-muted-foreground">They didn&apos;t create any assistants.</p>
          )}
          <ul className="text-muted-foreground list-disc pl-5">
            {leaving.pending_approvals > 0 && (
              <li>
                {leaving.pending_approvals} pending approval
                {leaving.pending_approvals === 1 ? "" : "s"} they asked for will be closed: nothing
                they asked for runs after they leave.
              </li>
            )}
            {leaving.open_invites > 0 && (
              <li>
                {leaving.open_invites} invite{leaving.open_invites === 1 ? "" : "s"} they sent will
                be withdrawn.
              </li>
            )}
            {leaving.editor_of.length > 0 && (
              <li>
                They stop being a co-editor of {leaving.editor_of.map((a) => a.name).join(", ")}.
              </li>
            )}
          </ul>
          {leaving.connections.length + leaving.mcp_servers.length > 0 && (
            <Alert>
              They could see the credentials of{" "}
              {[...leaving.connections, ...leaving.mcp_servers]
                .map((c) => `${c.name} (${c.assistant})`)
                .join(", ")}
              . Rotate those passwords and keys.
            </Alert>
          )}
        </div>
      )}
    </Dialog>
  );
}
