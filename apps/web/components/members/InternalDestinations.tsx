"use client";

import { Plus, Trash2 } from "lucide-react";
import { useId } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { InternalDestination } from "@/lib/api";

const EMPTY: InternalDestination = { host: "", cidr: "", ports: [443], http: false, note: "" };

/** "443, 8443" -> [443, 8443]; anything else is dropped (the server checks). */
export function parsePorts(raw: string): number[] {
  const out: number[] = [];
  for (const part of raw.split(/[\s,]+/)) {
    const n = Number(part);
    if (Number.isInteger(n) && n >= 1 && n <= 65535 && !out.includes(n)) out.push(n);
  }
  return out;
}

/** Systems on the office network or VPN the assistants may reach (Phase
 *  9.3): an intranet wiki for URL sources, a ticketing API, an MCP server
 *  inside. Each entry names the host and the address range it must resolve
 *  into, so a name that suddenly points somewhere else is still refused. */
export function InternalDestinations({
  value,
  editable,
  onChange,
}: {
  value: InternalDestination[];
  editable: boolean;
  onChange: (next: InternalDestination[]) => void;
}) {
  const id = useId();
  const set = (i: number, patch: Partial<InternalDestination>) =>
    onChange(value.map((d, j) => (j === i ? { ...d, ...patch } : d)));

  return (
    <fieldset className="flex flex-col gap-3">
      <legend className="mb-1">Internal destinations</legend>
      <p id={`${id}-hint`} className="text-small text-muted-foreground">
        Company systems the assistants may reach on your network. Everything else stays public-only.
        A host is a name (tickets.corp.example.com), a suffix with a leading dot (.corp.example.com)
        or an address; it has to resolve into the address range. This machine and link-local
        addresses are never allowed.
      </p>
      {value.length === 0 && (
        <p className="text-small text-muted-foreground">None: only public addresses are reached.</p>
      )}
      {value.map((d, i) => (
        <div
          key={i}
          className="border-border flex flex-col gap-2 rounded-md border p-3"
          aria-describedby={`${id}-hint`}
        >
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex min-w-[12rem] flex-[2] flex-col gap-1">
              <Label htmlFor={`${id}-${i}-host`}>Host</Label>
              <Input
                id={`${id}-${i}-host`}
                className="font-mono"
                required
                maxLength={253}
                disabled={!editable}
                placeholder="tickets.corp.example.com"
                value={d.host}
                onChange={(e) => set(i, { host: e.target.value })}
              />
            </div>
            <div className="flex min-w-[9rem] flex-1 flex-col gap-1">
              <Label htmlFor={`${id}-${i}-cidr`}>Address range</Label>
              <Input
                id={`${id}-${i}-cidr`}
                className="font-mono"
                required
                maxLength={49}
                disabled={!editable}
                placeholder="10.20.0.0/16"
                value={d.cidr}
                onChange={(e) => set(i, { cidr: e.target.value })}
              />
            </div>
            <div className="flex w-28 flex-col gap-1">
              <Label htmlFor={`${id}-${i}-ports`}>Ports</Label>
              <Input
                id={`${id}-${i}-ports`}
                className="font-mono"
                disabled={!editable}
                // Re-mount when the parsed value changes, so the box shows it.
                key={d.ports.join(",")}
                defaultValue={d.ports.join(", ")}
                onBlur={(e) => {
                  const ports = parsePorts(e.target.value);
                  set(i, { ports: ports.length ? ports : [443] });
                }}
              />
            </div>
            {editable && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={`Remove ${d.host || "this destination"}`}
                onClick={() => onChange(value.filter((_, j) => j !== i))}
              >
                <Trash2 aria-hidden className="size-4" />
              </Button>
            )}
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex min-w-[12rem] flex-1 flex-col gap-1">
              <Label htmlFor={`${id}-${i}-note`}>What it is</Label>
              <Input
                id={`${id}-${i}-note`}
                maxLength={200}
                disabled={!editable}
                placeholder="IT's ticketing system"
                value={d.note ?? ""}
                onChange={(e) => set(i, { note: e.target.value })}
              />
            </div>
            <label className="flex h-9 items-center gap-2">
              <input
                type="checkbox"
                className="size-4"
                disabled={!editable}
                checked={d.http}
                onChange={(e) => set(i, { http: e.target.checked })}
              />
              Allow plain http
            </label>
          </div>
        </div>
      ))}
      {editable && (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="self-start"
          disabled={value.length >= 50}
          onClick={() => onChange([...value, { ...EMPTY, ports: [443] }])}
        >
          <Plus aria-hidden className="size-4" />
          Add a destination
        </Button>
      )}
    </fieldset>
  );
}
