"use client";

import { useEffect, useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SectionHeading } from "@/components/ui/section-heading";
import { ApiError, assistants, orgs, type Group, type Listing, type Member } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const MAX_STARTERS = 4;

/** The draft listing (Phase 8.5): how Chat's catalog shows the assistant,
 *  and who it is for. Saved here, it reaches Chat with the next approved
 *  version, and the admin reviewing sees it. */
export function ListingEditor({ assistantId, canEdit }: { assistantId: string; canEdit: boolean }) {
  const { activeOrgId } = useAuth();
  const [listing, setListing] = useState<Listing | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [people, setPeople] = useState<Member[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const id = useId();

  useEffect(() => {
    assistants
      .listing(assistantId)
      .then(setListing)
      .catch(() => setListing(null));
    if (activeOrgId) {
      orgs
        .groups(activeOrgId)
        .then(setGroups)
        .catch(() => setGroups([]));
      orgs
        .members(activeOrgId)
        .then((p) => setPeople(p.items.filter((m) => m.is_active)))
        .catch(() => setPeople([]));
    }
  }, [assistantId, activeOrgId]);

  if (!listing) return null;
  const audience = listing.audience ?? { kind: "everyone", group_ids: [], user_ids: [] };
  const starters = [...(listing.starters ?? [])];
  while (starters.length < MAX_STARTERS) starters.push("");

  const setAudience = (patch: Partial<typeof audience>) =>
    setListing({ ...listing, audience: { ...audience, ...patch } });
  const toggle = (list: string[] | undefined, value: string) =>
    (list ?? []).includes(value)
      ? (list ?? []).filter((v) => v !== value)
      : [...(list ?? []), value];

  const save = async () => {
    setBusy(true);
    setNote(null);
    try {
      setListing(
        await assistants.saveListing(assistantId, {
          ...listing,
          starters: starters.map((s) => s.trim()).filter(Boolean),
        }),
      );
      setNote({
        ok: true,
        text: "Saved. Chat shows it once the next version you submit is approved.",
      });
    } catch (err) {
      setNote({
        ok: false,
        text: err instanceof ApiError ? err.message : "Couldn't reach the server. Try again.",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby={`${id}-heading`} className="flex max-w-160 flex-col gap-6">
      <SectionHeading
        level={2}
        id={`${id}-heading`}
        title="Listing"
        description="How Chat's catalog shows this assistant, and who it is for. It reaches Chat with the next approved version: the admin reviewing sees it."
      />

      <div className="flex max-w-80 flex-col gap-1.5">
        <Label htmlFor={`${id}-category`}>Category</Label>
        <Input
          id={`${id}-category`}
          maxLength={40}
          placeholder="e.g. IT, HR, Finance"
          disabled={!canEdit}
          value={listing.category ?? ""}
          onChange={(e) => setListing({ ...listing, category: e.target.value })}
        />
      </div>

      <fieldset className="flex flex-col gap-2" disabled={!canEdit}>
        <legend className="text-label mb-1 font-medium">Conversation starters</legend>
        <p className="text-small text-muted-foreground">
          Up to four questions that show people what to ask. Shown in a new conversation.
        </p>
        {starters.map((s, i) => (
          <Input
            key={i}
            aria-label={`Starter ${i + 1}`}
            maxLength={200}
            value={s}
            onChange={(e) => {
              const next = [...starters];
              next[i] = e.target.value;
              setListing({ ...listing, starters: next });
            }}
          />
        ))}
      </fieldset>

      <fieldset className="flex flex-col gap-2" disabled={!canEdit}>
        <legend className="text-label mb-1 font-medium">Who it is for</legend>
        <p className="text-small text-muted-foreground">
          Admins, the owner and this assistant&apos;s builders always see it.
        </p>
        {(["everyone", "groups", "people"] as const).map((kind) => (
          <label key={kind} className="flex items-center gap-2">
            <input
              type="radio"
              name={`${id}-audience`}
              checked={audience.kind === kind}
              onChange={() => setAudience({ kind })}
            />
            {kind === "everyone"
              ? "Everyone in the organisation"
              : kind === "groups"
                ? "Members of groups"
                : "Named people"}
          </label>
        ))}
        {audience.kind === "groups" && (
          <div className="border-border ml-6 flex flex-col gap-1 border-l pl-3">
            {groups.length === 0 && (
              <p className="text-small text-muted-foreground">
                No groups yet. An admin makes them on the Groups page.
              </p>
            )}
            {groups.map((g) => (
              <label key={g.id} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={(audience.group_ids ?? []).includes(g.id)}
                  onChange={() => setAudience({ group_ids: toggle(audience.group_ids, g.id) })}
                />
                {g.name}
                <span className="text-small text-muted-foreground">({g.members})</span>
              </label>
            ))}
          </div>
        )}
        {audience.kind === "people" && (
          <div className="border-border ml-6 flex max-h-60 flex-col gap-1 overflow-y-auto border-l pl-3">
            {people.map((m) => (
              <label key={m.user_id} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={(audience.user_ids ?? []).includes(m.user_id)}
                  onChange={() => setAudience({ user_ids: toggle(audience.user_ids, m.user_id) })}
                />
                {m.name ? `${m.name} (${m.email})` : m.email}
              </label>
            ))}
          </div>
        )}
      </fieldset>

      {note && <Alert tone={note.ok ? "success" : undefined}>{note.text}</Alert>}
      {canEdit && (
        <Button className="self-start" disabled={busy} onClick={() => void save()}>
          {busy ? "Saving…" : "Save listing"}
        </Button>
      )}
    </section>
  );
}
