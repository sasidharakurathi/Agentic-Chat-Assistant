"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError, assistants, orgs, type Assistant } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { audienceWords } from "@/lib/listing";

type Shown = {
  category?: string;
  starters?: string[];
  audience?: { kind: "everyone" | "groups" | "people"; group_ids?: string[]; user_ids?: string[] };
} | null;

/** What a version will show in Chat, and to whom (Phase 8.5): for the
 *  admin reviewing it, beside what changed. */
export function VersionListingSummary({
  assistantId,
  number,
}: {
  assistantId: string;
  number: number;
}) {
  const { activeOrgId } = useAuth();
  const [shown, setShown] = useState<Shown>(null);
  const [groups, setGroups] = useState<Record<string, string>>({});
  const [people, setPeople] = useState<Record<string, string>>({});

  useEffect(() => {
    assistants
      .version(assistantId, number)
      .then((v) => setShown((v as { listing?: Shown }).listing ?? null))
      .catch(() => setShown(null));
  }, [assistantId, number]);

  useEffect(() => {
    if (!activeOrgId) return;
    orgs
      .groups(activeOrgId)
      .then((gs) => setGroups(Object.fromEntries(gs.map((g) => [g.id, g.name]))))
      .catch(() => setGroups({}));
    orgs
      .members(activeOrgId)
      .then((p) =>
        setPeople(Object.fromEntries(p.items.map((m) => [m.user_id, m.name || m.email]))),
      )
      .catch(() => setPeople({}));
  }, [activeOrgId]);

  return (
    <div className="border-border mb-6 flex flex-col gap-1 rounded-md border p-4">
      <p className="font-medium">Version {number} in Chat</p>
      <p className="text-small">
        <span className="text-muted-foreground">For: </span>
        {audienceWords(shown?.audience, groups, people)}
      </p>
      {shown?.category && (
        <p className="text-small">
          <span className="text-muted-foreground">Category: </span>
          {shown.category}
        </p>
      )}
      {shown?.starters && shown.starters.length > 0 && (
        <p className="text-small">
          <span className="text-muted-foreground">Starters: </span>
          {shown.starters.join("; ")}
        </p>
      )}
    </div>
  );
}

/** Official (Phase 8.5): an admin's mark, shown first in the catalog. */
export function OfficialToggle({
  assistant,
  onChanged,
}: {
  assistant: Assistant;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const official = Boolean(assistant.official_at);

  const flip = async () => {
    setBusy(true);
    setError(null);
    try {
      await assistants.setOfficial(assistant.id, !official);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach the server.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border-border mt-4 flex flex-col gap-2 border-t pt-4">
      <div className="flex items-center gap-2">
        <span className="font-medium">Catalog</span>
        {official && <Badge variant="success">Official</Badge>}
      </div>
      <Button
        variant="outline"
        size="sm"
        className="self-start"
        disabled={busy}
        onClick={() => void flip()}
      >
        {official ? "Remove the Official mark" : "Mark as Official"}
      </Button>
      {error && <p className="text-small text-destructive">{error}</p>}
    </div>
  );
}
