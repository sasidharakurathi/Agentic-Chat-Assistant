"use client";

import { useId, useRef, useState } from "react";

import type { StationConnections } from "@/components/canvas/graph-sync";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";

/** A station's lines, as a list (task 6.8). On the canvas a line is drawn
 *  by dragging between two small handles, which needs a pointer. This is
 *  the same thing for a keyboard or a screen reader: what the station
 *  goes to and comes from, a Disconnect for each, and a list of what it
 *  may be connected to. The choices are the ones the canvas would accept
 *  a drag to, from the same wiring rules.
 *
 *  After connecting or disconnecting, focus returns to the heading: the
 *  button that was pressed may no longer exist. */
export function Connections({
  name,
  connections,
  onConnect,
  onDisconnect,
}: {
  /** The station these belong to, for the controls' names. */
  name: string;
  connections: StationConnections;
  onConnect: (target: string) => void;
  onDisconnect: (source: string, target: string, self: "source" | "target") => void;
}) {
  const ids = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const [choice, setChoice] = useState("");
  const { outgoing, incoming, targets } = connections;
  const target = targets.find((t) => t.id === choice) ?? targets[0];
  const back = () => heading.current?.focus();

  return (
    <section
      aria-labelledby={`${ids}-h`}
      className="border-border mt-6 flex flex-col gap-3 border-t pt-4"
    >
      <h3
        id={`${ids}-h`}
        ref={heading}
        tabIndex={-1}
        className="text-h4 font-semibold focus-visible:rounded-sm"
      >
        Connections
      </h3>

      {outgoing.length + incoming.length === 0 && (
        <p className="text-small text-muted-foreground">Not connected to anything.</p>
      )}
      {outgoing.length > 0 && (
        <Lines
          title="Goes to"
          items={outgoing}
          action={(other) => `Disconnect ${name} from ${other}`}
          onRemove={(id) => {
            onDisconnect(connections.id, id, "source");
            back();
          }}
        />
      )}
      {incoming.length > 0 && (
        <Lines
          title="Comes from"
          items={incoming}
          action={(other) => `Disconnect ${other} from ${name}`}
          onRemove={(id) => {
            onDisconnect(id, connections.id, "target");
            back();
          }}
        />
      )}

      {targets.length > 0 && target && (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${ids}-to`}>Connect to</Label>
          <div className="flex gap-2">
            <Select id={`${ids}-to`} value={target.id} onChange={(e) => setChoice(e.target.value)}>
              {targets.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </Select>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-9 shrink-0"
              aria-label={`Connect ${name} to ${target.label}`}
              onClick={() => {
                onConnect(target.id);
                setChoice("");
                back();
              }}
            >
              Connect
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

function Lines({
  title,
  items,
  action,
  onRemove,
}: {
  title: string;
  items: { id: string; label: string }[];
  action: (other: string) => string;
  onRemove: (id: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <p className="text-small text-muted-foreground">{title}</p>
      <ul className="flex flex-col">
        {items.map((item) => (
          <li key={item.id} className="flex items-center justify-between gap-2">
            <span className="min-w-0 truncate text-sm">{item.label}</span>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="-mr-2 shrink-0"
              aria-label={action(item.label)}
              onClick={() => onRemove(item.id)}
            >
              Disconnect
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
