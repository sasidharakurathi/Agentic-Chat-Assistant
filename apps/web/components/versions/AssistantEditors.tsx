"use client";

import { useCallback, useEffect, useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { ApiError, assistants, orgs, type AssistantDetail, type Member } from "@/lib/api";
import { isAdminRole } from "@/lib/assistant-lifecycle";
import { useAuth } from "@/lib/auth";
import type { Schemas } from "@assistant-studio/shared";

type Editor = Schemas["EditorOut"];

/** Who besides its creator may edit an assistant (Phase 7b.4). Everyone
 *  sees the list; its creator or an admin adds and removes people. */
export function AssistantEditors({ assistant }: { assistant: AssistantDetail }) {
  const { user, activeRole, activeOrgId } = useAuth();
  const manage =
    isAdminRole(activeRole) || (Boolean(user?.id) && assistant.created_by === user?.id);
  const [editors, setEditors] = useState<Editor[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [pick, setPick] = useState("");
  const [error, setError] = useState<string | null>(null);
  const pickId = useId();

  const load = useCallback(async () => {
    try {
      setEditors(await assistants.editors(assistant.id));
    } catch {
      /* the list is a convenience; the server still decides who edits */
    }
  }, [assistant.id]);

  useEffect(() => {
    void load();
    if (manage && activeOrgId) {
      orgs
        .members(activeOrgId)
        .then((p) => setMembers(p.items))
        .catch(() => {});
    }
  }, [load, manage, activeOrgId]);

  const candidates = members.filter(
    (m) =>
      m.is_active !== false &&
      m.user_id !== assistant.created_by &&
      !editors.some((e) => e.user_id === m.user_id),
  );

  const run = async (what: string, call: () => Promise<unknown>) => {
    setError(null);
    try {
      await call();
      await load();
      setPick("");
    } catch (err) {
      setError(`Couldn't ${what}. ${err instanceof ApiError ? err.message : "Try again."}`);
    }
  };

  return (
    <section aria-labelledby={`${pickId}-heading`} className="mt-6">
      <h3 id={`${pickId}-heading`} className="text-sm font-medium">
        Who can edit
      </h3>
      <p className="text-small text-muted-foreground">
        Its creator, admins
        {editors.length > 0 ? ", and:" : ". Add co-editors to keep it maintained."}
      </p>
      {editors.length > 0 && (
        <ul className="border-border mt-2 border-t">
          {editors.map((e) => (
            <li key={e.user_id} className="border-border flex items-center gap-2 border-b py-2">
              <span className="text-small min-w-0 flex-1 truncate" title={e.email}>
                {e.name || e.email}
              </span>
              {manage && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Stop ${e.name || e.email} editing`}
                  onClick={() =>
                    void run("remove the co-editor", () =>
                      assistants.removeEditor(assistant.id, e.user_id),
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
      {manage && candidates.length > 0 && (
        <div className="mt-2 flex items-end gap-2">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <Label htmlFor={pickId} className="sr-only">
              Add a co-editor
            </Label>
            <Select id={pickId} value={pick} onChange={(e) => setPick(e.target.value)}>
              <option value="">Add a co-editor…</option>
              {candidates.map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  {m.name || m.email}
                </option>
              ))}
            </Select>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!pick}
            onClick={() =>
              void run("add the co-editor", () => assistants.addEditor(assistant.id, pick))
            }
          >
            Add
          </Button>
        </div>
      )}
      {error && (
        <Alert className="mt-2" role="alert">
          {error}
        </Alert>
      )}
    </section>
  );
}
