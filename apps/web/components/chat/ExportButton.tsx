"use client";

import { Download } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { ApiError, exportConversation } from "@/lib/api";

/** Save the conversation as a Markdown file (Phase 10.5): its messages and
 *  their sources, as you see them here. Anyone who may read it may export
 *  it; an admin's export goes with the reason they gave for opening it. */
export function ExportButton({ conversationId }: { conversationId: string }) {
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const save = async () => {
    setBusy(true);
    try {
      const { blob, filename } = await exportConversation(conversationId, "md");
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast(
        err instanceof ApiError
          ? err.message
          : "Couldn't save the conversation. Check your connection, then try again.",
        "error",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Button variant="ghost" size="sm" disabled={busy} onClick={() => void save()}>
      <Download aria-hidden className="size-4" />
      {busy ? "Saving…" : "Export"}
    </Button>
  );
}
