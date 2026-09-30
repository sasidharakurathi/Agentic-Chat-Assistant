"use client";

import { useState } from "react";

import { RunTrace } from "@/components/chat/RunTrace";

/** "Run details" under an answer: opens the run's trace in a side drawer
 *  (task 5.9). Fetched only when opened: most answers are never inspected. */
export function RunDetails({
  conversationId,
  messageId,
}: {
  conversationId: string;
  messageId: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="text-xs">
      <button
        className="text-muted-foreground hover:text-foreground"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        Run details
      </button>
      {open && (
        <RunTrace
          conversationId={conversationId}
          messageId={messageId}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}
