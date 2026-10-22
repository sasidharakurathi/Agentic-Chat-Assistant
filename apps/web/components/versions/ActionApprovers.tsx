"use client";

import { useCallback, useEffect, useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import {
  ApiError,
  approvals,
  orgs,
  type Approvers,
  type AssistantDetail,
  type Group,
  type Member,
} from "@/lib/api";
import { isAdminRole } from "@/lib/assistant-lifecycle";
import { useAuth } from "@/lib/auth";

/** Who decides this assistant's requests (Phase 9.1): named groups and
 *  people, or the admins when nobody is named. Its editors see it; an
 *  admin changes it. Being an approver doesn't make someone an admin: they
 *  see and decide these requests, not every conversation. */
export function ActionApprovers({ assistant }: { assistant: AssistantDetail }) {
  const { activeRole, activeOrgId } = useAuth();
  const admin = isAdminRole(activeRole);
  const id = useId();
  const [current, setCurrent] = useState<Approvers | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [pickGroup, setPickGroup] = useState("");
  const [pickPerson, setPickPerson] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCurrent(await approvals.approvers(assistant.id));
    } catch {
      setCurrent(null);
    }
  }, [assistant.id]);

  useEffect(() => {
    void load();
    if (admin && activeOrgId) {
      orgs
        .groups(activeOrgId)
        .then(setGroups)
        .catch(() => {});
      orgs
        .members(activeOrgId)
        .then((p) => setMembers(p.items.filter((m) => m.is_active !== false)))
        .catch(() => {});
    }
  }, [load, admin, activeOrgId]);

  if (!current) return null;

  const save = async (group_ids: string[], user_ids: string[]) => {
    setError(null);
    try {
      setCurrent(await approvals.setApprovers(assistant.id, { group_ids, user_ids }));
      setPickGroup("");
      setPickPerson("");
    } catch (err) {
      setError(`Couldn't save. ${err instanceof ApiError ? err.message : "Try again."}`);
    }
  };

  const groupName = (gid: string) => groups.find((g) => g.id === gid)?.name ?? "A group";
  const personName = (uid: string) => {
    const m = members.find((x) => x.user_id === uid);
    const p = current.people.find((x) => x.user_id === uid);
    return m?.name || m?.email || p?.name || p?.email || "Someone";
  };

  return (
    <section aria-labelledby={`${id}-heading`} className="mt-6">
      <h3 id={`${id}-heading`} className="text-sm font-medium">
        Who approves its requests
      </h3>
      <p className="text-small text-muted-foreground">
        {current.admins_decide
          ? "The admins, as nobody is named. Requests that need an approver go to them."
          : "These people decide its requests (admins can too). Nobody decides their own."}
      </p>
      {(current.group_ids.length > 0 || current.user_ids.length > 0) && (
        <ul className="border-border mt-2 border-t">
          {current.group_ids.map((gid) => (
            <li key={gid} className="border-border flex items-center gap-2 border-b py-2">
              <span className="text-small min-w-0 flex-1 truncate">
                {groupName(gid)} <span className="text-muted-foreground">(group)</span>
              </span>
              {admin && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    void save(
                      current.group_ids.filter((g) => g !== gid),
                      current.user_ids,
                    )
                  }
                >
                  Remove
                </Button>
              )}
            </li>
          ))}
          {current.user_ids.map((uid) => (
            <li key={uid} className="border-border flex items-center gap-2 border-b py-2">
              <span className="text-small min-w-0 flex-1 truncate">{personName(uid)}</span>
              {admin && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    void save(
                      current.group_ids,
                      current.user_ids.filter((u) => u !== uid),
                    )
                  }
                >
                  Remove
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {admin && (
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div className="flex min-w-[12rem] flex-1 flex-col gap-1">
            <Label htmlFor={`${id}-group`}>Add a group</Label>
            <Select
              id={`${id}-group`}
              value={pickGroup}
              onChange={(e) => {
                setPickGroup(e.target.value);
                if (e.target.value)
                  void save([...current.group_ids, e.target.value], current.user_ids);
              }}
            >
              <option value="">Choose a group</option>
              {groups
                .filter((g) => !current.group_ids.includes(g.id))
                .map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
            </Select>
          </div>
          <div className="flex min-w-[12rem] flex-1 flex-col gap-1">
            <Label htmlFor={`${id}-person`}>Add a person</Label>
            <Select
              id={`${id}-person`}
              value={pickPerson}
              onChange={(e) => {
                setPickPerson(e.target.value);
                if (e.target.value)
                  void save(current.group_ids, [...current.user_ids, e.target.value]);
              }}
            >
              <option value="">Choose a person</option>
              {members
                .filter((m) => !current.user_ids.includes(m.user_id))
                .map((m) => (
                  <option key={m.user_id} value={m.user_id}>
                    {m.name || m.email}
                  </option>
                ))}
            </Select>
          </div>
        </div>
      )}
      {error && (
        <Alert className="mt-3" role="alert">
          {error}
        </Alert>
      )}
    </section>
  );
}
