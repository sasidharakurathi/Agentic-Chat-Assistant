"use client";

import { useCallback, useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Loading } from "@/components/ui/loading";
import { PageHeader } from "@/components/ui/page-header";
import { Select } from "@/components/ui/select";
import { ApiError, orgs, type Group, type GroupMember, type Member } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function said(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/** Groups (Phase 8.5): who an assistant's audience can name. Admins make
 *  them and choose who is in them; a change reaches Chat at once. */
export default function GroupsPage() {
  const { activeOrgId } = useAuth();
  const confirm = useConfirm();
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [people, setPeople] = useState<Member[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [members, setMembers] = useState<GroupMember[]>([]);
  const [name, setName] = useState("");
  const [adding, setAdding] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!activeOrgId) return;
    try {
      setGroups(await orgs.groups(activeOrgId));
    } catch (err) {
      setError(said(err, "Couldn't load the groups."));
    }
  }, [activeOrgId]);

  useEffect(() => {
    void load();
    if (activeOrgId)
      orgs
        .members(activeOrgId)
        .then((p) => setPeople(p.items.filter((m) => m.is_active)))
        .catch(() => setPeople([]));
  }, [load, activeOrgId]);

  useEffect(() => {
    if (!activeOrgId || !open) return;
    orgs
      .groupMembers(activeOrgId, open)
      .then(setMembers)
      .catch((err) => setError(said(err, "Couldn't load the group's members.")));
  }, [activeOrgId, open]);

  if (!activeOrgId) return null;

  const act = async (work: () => Promise<void>, fallback: string) => {
    setError(null);
    try {
      await work();
      await load();
    } catch (err) {
      setError(said(err, fallback));
    }
  };

  const current = groups?.find((g) => g.id === open) ?? null;
  const inGroup = new Set(members.map((m) => m.user_id));

  return (
    <div className="mx-auto w-full max-w-240 px-4 py-8 md:px-6 lg:px-8">
      <PageHeader
        title="Groups"
        description="Who an assistant can be for. Adding or removing someone reaches Chat at once; an assistant's audience names the group, not its members."
      />
      {error && <Alert className="mb-6">{error}</Alert>}

      <form
        className="mb-8 flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void act(async () => {
            await orgs.createGroup(activeOrgId, name.trim());
            setName("");
          }, "Couldn't make the group.");
        }}
      >
        <div className="flex w-64 flex-col gap-1.5">
          <Label htmlFor="group-name">New group</Label>
          <Input
            id="group-name"
            maxLength={80}
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. IT, Finance, New joiners"
          />
        </div>
        <Button type="submit" disabled={!name.trim()}>
          Make group
        </Button>
      </form>

      {groups === null ? (
        <Loading what="groups" rows={3} />
      ) : groups.length === 0 ? (
        <p className="text-muted-foreground border-border border-t pt-3">No groups yet.</p>
      ) : (
        <div className="grid gap-8 md:grid-cols-[16rem_1fr]">
          <ul className="border-border border-t">
            {groups.map((g) => (
              <li key={g.id} className="border-border border-b">
                <button
                  type="button"
                  aria-current={open === g.id}
                  onClick={() => setOpen(g.id)}
                  className="hover:bg-muted aria-[current=true]:bg-muted flex w-full items-baseline justify-between gap-3 px-2 py-2.5 text-left"
                >
                  <span className="truncate font-medium">{g.name}</span>
                  <span className="text-small text-muted-foreground">{g.members}</span>
                </button>
              </li>
            ))}
          </ul>

          {current && (
            <section aria-label={current.name} className="flex flex-col gap-4">
              <div className="flex flex-wrap items-end gap-3">
                <div className="flex w-72 flex-col gap-1.5">
                  <Label htmlFor="group-add">Add someone</Label>
                  <Select id="group-add" value={adding} onChange={(e) => setAdding(e.target.value)}>
                    <option value="">Choose a person</option>
                    {people
                      .filter((p) => !inGroup.has(p.user_id))
                      .map((p) => (
                        <option key={p.user_id} value={p.user_id}>
                          {p.name ? `${p.name} (${p.email})` : p.email}
                        </option>
                      ))}
                  </Select>
                </div>
                <Button
                  disabled={!adding}
                  onClick={() =>
                    void act(async () => {
                      setMembers(await orgs.addGroupMember(activeOrgId, current.id, adding));
                      setAdding("");
                    }, "Couldn't add them.")
                  }
                >
                  Add
                </Button>
              </div>
              <ul className="border-border border-t">
                {members.length === 0 && (
                  <li className="text-muted-foreground py-3">Nobody in this group yet.</li>
                )}
                {members.map((m) => (
                  <li
                    key={m.user_id}
                    className="border-border flex items-center justify-between gap-3 border-b py-2"
                  >
                    <span className="truncate">{m.name ? `${m.name} (${m.email})` : m.email}</span>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        void act(async () => {
                          setMembers(
                            await orgs.removeGroupMember(activeOrgId, current.id, m.user_id),
                          );
                        }, "Couldn't take them out.")
                      }
                    >
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
              <Button
                variant="outline"
                size="sm"
                className="self-start"
                onClick={() =>
                  void (async () => {
                    const ok = await confirm({
                      title: `Delete ${current.name}?`,
                      description:
                        "Its members leave it. An assistant that is for this group must be changed first.",
                      confirmLabel: "Delete group",
                      destructive: true,
                    });
                    if (!ok) return;
                    await act(async () => {
                      await orgs.deleteGroup(activeOrgId, current.id);
                      setOpen(null);
                    }, "Couldn't delete the group.");
                  })()
                }
              >
                Delete group
              </Button>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
