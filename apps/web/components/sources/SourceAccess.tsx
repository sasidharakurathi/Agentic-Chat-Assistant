"use client";

import { useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { DataSource, Group } from "@/lib/api";

/** Who may read a knowledge source (Phase 9.4): everyone, or groups.
 *  Deny by default: no groups and not everyone is nobody. */

export function accessLabel(row: DataSource, groups: Group[] | null): string {
  if (row.everyone !== false) return "Everyone";
  const ids = row.group_ids ?? [];
  if (ids.length === 0) return "Nobody yet";
  if (!groups) return `${ids.length} group${ids.length === 1 ? "" : "s"}`;
  const names = ids.map((id) => groups.find((g) => g.id === id)?.name ?? "a deleted group");
  return names.join(", ");
}

export function SourceAccessEditor({
  row,
  groups,
  busy,
  onSave,
  onCancel,
}: {
  row: DataSource;
  groups: Group[];
  busy: boolean;
  onSave: (access: { everyone: boolean; group_ids: string[] }) => void;
  onCancel: () => void;
}) {
  const id = useId();
  const [everyone, setEveryone] = useState(row.everyone !== false);
  const [picked, setPicked] = useState<string[]>(
    (row.group_ids ?? []).filter((g) => groups.some((x) => x.id === g)),
  );
  const toggle = (g: string) =>
    setPicked((p) => (p.includes(g) ? p.filter((x) => x !== g) : [...p, g]));

  return (
    <form
      className="border-border flex flex-col gap-3 rounded-lg border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({ everyone, group_ids: everyone ? [] : picked });
      }}
    >
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 font-medium">Who can read it</legend>
        <label className="flex items-center gap-2">
          <input
            type="radio"
            name={`${id}-who`}
            className="size-4"
            checked={everyone}
            onChange={() => setEveryone(true)}
          />
          Everyone who can use the assistant
        </label>
        <label className="flex items-center gap-2">
          <input
            type="radio"
            name={`${id}-who`}
            className="size-4"
            checked={!everyone}
            onChange={() => setEveryone(false)}
          />
          Only these groups
        </label>
        {!everyone && (
          <div className="ml-6 flex flex-col gap-1.5">
            {groups.length === 0 ? (
              <p className="text-small text-muted-foreground">
                There are no groups yet. An admin makes them under Groups.
              </p>
            ) : (
              groups.map((g) => (
                <label key={g.id} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    className="size-4"
                    checked={picked.includes(g.id)}
                    onChange={() => toggle(g.id)}
                  />
                  {g.name}
                </label>
              ))
            )}
          </div>
        )}
      </fieldset>
      <p className="text-small text-muted-foreground">
        Answers and citations use it only for people who can read it, and the assistant is only
        offered to people who can read all of its restricted sources. Someone who loses access
        starts their conversations afresh.
      </p>
      {!everyone && picked.length === 0 && (
        <Alert tone="warning">With no groups, nobody can read it.</Alert>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy}>
          Save who can read it
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

const KIND_WORDS: Record<string, string> = {
  secret: "credentials",
  aadhaar: "Aadhaar numbers",
  pan: "PAN numbers",
  card: "card numbers",
  phone: "phone numbers",
  email: "email addresses",
  ssn: "US social security numbers",
};

type Scan = {
  counts?: Record<string, number>;
  samples?: { kind: string; value: string }[];
  reason?: string;
};

/** What the scan at ingest found, in words: "2 credentials, 14 phone
 *  numbers". */
export function scanSummary(scan: Scan | null | undefined): string {
  const counts = scan?.counts ?? {};
  return Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${n} ${KIND_WORDS[k] ?? k}`)
    .join(", ");
}

/** A source the scan held back (Phase 9.4): what it found, masked, and the
 *  two ways on. */
export function HeldNotice({
  row,
  busy,
  onConfirm,
  onChooseGroups,
}: {
  row: DataSource;
  busy: boolean;
  onConfirm: () => void;
  onChooseGroups: () => void;
}) {
  const scan = (row.ingest_report?.sensitive ?? null) as Scan | null;
  return (
    <Alert tone="warning">
      <div className="flex flex-col gap-2">
        <p>
          Not indexed yet: {scan?.reason ?? "it holds personal or secret data"}, and everyone could
          read it. Give it to groups, or confirm everyone may read it.
        </p>
        {(scan?.samples ?? []).length > 0 && (
          <p className="text-small">
            Found: {scanSummary(scan)}. For example{" "}
            {(scan?.samples ?? []).slice(0, 4).map((s, i) => (
              <span key={i}>
                {i > 0 && ", "}
                <span className="font-mono">{s.value}</span>
              </span>
            ))}
            .
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={onChooseGroups} disabled={busy}>
            Choose groups
          </Button>
          <Button size="sm" variant="outline" onClick={onConfirm} disabled={busy}>
            Everyone may read it
          </Button>
        </div>
      </div>
    </Alert>
  );
}
