"use client";

import { Route } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { RunTrace } from "@/components/chat/RunTrace";
import { buttonVariants } from "@/components/ui/button";

/** "Run details" under an answer: opens the run's trace in a side sheet.
 *  Fetched only when opened, since most answers are never inspected. When
 *  the sheet closes, focus goes back to this button. */
export function RunDetails({
  conversationId,
  messageId,
}: {
  conversationId: string;
  messageId: string;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  // Back to the trigger once the sheet is gone: while the modal is still
  // open, everything behind it is inert and can't take focus.
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open) {
      wasOpen.current = true;
    } else if (wasOpen.current) {
      wasOpen.current = false;
      trigger.current?.focus();
    }
  }, [open]);
  return (
    <div>
      <button
        ref={trigger}
        type="button"
        className={buttonVariants({
          variant: "ghost",
          size: "sm",
          className: "text-muted-foreground hover:text-foreground -ml-3",
        })}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((o) => !o)}
      >
        <Route aria-hidden />
        Run details
      </button>
      {open && <RunTrace conversationId={conversationId} messageId={messageId} onClose={close} />}
    </div>
  );
}
