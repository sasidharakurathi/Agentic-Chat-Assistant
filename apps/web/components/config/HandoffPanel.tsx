"use client";

import { useEffect, useState } from "react";

import { Field, Toggle } from "@/components/config/panels";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import type { AssistantConfig } from "@/lib/api";

type Data = Record<string, unknown>;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Hand-off to a person (Phase 9.5), in Settings: who is told, and
 *  optionally the HTTP action that opens a ticket instead. Kept on the
 *  agent node; approved with the version like everything else. */
export function HandoffPanel({
  config,
  onChange,
}: {
  config: AssistantConfig;
  onChange: (next: AssistantConfig) => void;
}) {
  const handoff = (config.handoff ?? {}) as Data;
  const on = handoff.enabled === true;
  const saved = String(handoff.contact_email ?? "");
  const action = String(handoff.action ?? "");
  const [email, setEmail] = useState(saved);
  useEffect(() => setEmail(saved), [saved]);
  const typed = email.trim();
  const valid = EMAIL.test(typed);

  const http = ((config.tools as Data | undefined)?.http_request ?? {}) as Data;
  const actions = (Array.isArray(http.actions) ? http.actions : []) as Data[];
  // An action that waits for an approver can't serve someone who needs help now.
  const usable = http.enabled === true ? actions.filter((a) => a.tier !== "approver") : [];
  const waiting = http.enabled === true ? actions.length - usable.length : 0;

  const save = (patch: Data) =>
    onChange({
      ...config,
      handoff: { enabled: on, contact_email: valid ? typed : saved, action, ...patch },
    } as AssistantConfig);

  return (
    <div className="@container flex flex-col gap-3">
      <Field
        label="Who is told"
        hint="An admin's address works best. The email only links to the conversation; they read it in the app, where an admin's read is recorded."
      >
        <Input
          type="email"
          value={email}
          placeholder="it-owner@example.com"
          onChange={(e) => setEmail(e.target.value)}
          onBlur={() => {
            if (typed === saved) return;
            if (valid || (!typed && !on)) save({ contact_email: typed });
          }}
        />
      </Field>
      {typed && !valid && (
        <p role="alert" className="text-small text-destructive">
          That isn&apos;t an email address.
        </p>
      )}
      {on && !typed && (
        <p role="alert" className="text-small text-destructive">
          Hand-off needs someone to tell. Type an address, or turn it off.
        </p>
      )}
      <Toggle
        label="Let people get help from a person"
        checked={on}
        disabled={!on && !valid}
        onChange={(v) => save({ enabled: v })}
        hint={
          valid || on
            ? "People see “Get help from a person” in Chat, and the assistant can hand off when it can't help."
            : "Type who is told first."
        }
      />
      {on && (
        <div className="border-border ml-1 flex flex-col gap-4 border-l-2 pl-4">
          <Field
            label="Open a ticket with"
            hint={
              waiting > 0
                ? "Actions that wait for an approver aren't listed: someone asking for help can't wait. A 'confirm' action is confirmed by the person's own click."
                : "An HTTP action that opens a ticket. It gets the conversation's title and a summary in its title and summary parameters, and the person's note in note."
            }
          >
            <Select value={action} onChange={(e) => save({ action: e.target.value })}>
              <option value="">No ticket: email the contact</option>
              {usable.map((a) => (
                <option key={String(a.name)} value={String(a.name)}>
                  {String(a.title || a.name)}
                </option>
              ))}
              {action && !usable.some((a) => a.name === action) && (
                <option value={action}>{action} (not available)</option>
              )}
            </Select>
          </Field>
        </div>
      )}
    </div>
  );
}
