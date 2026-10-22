"use client";

import { ImagePlus, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ClipboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { ApiError, attachments as attachmentsApi } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Screenshots in chat (Phase 9.6): the most common helpdesk input.
 *
 *  A screenshot is picked or pasted into the composer, uploaded into the
 *  conversation straight away, and shown as a thumbnail until it is sent
 *  with the next message. The server decides what a file is from its bytes;
 *  the checks here only save a pointless upload. */

const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
export const MAX_IMAGES = 4;

export type PendingShot = {
  /** This page's own key for it. */
  key: string;
  /** The picked or pasted file, shown before (and while) it uploads. */
  preview: string;
  status: "uploading" | "ready" | "failed";
  /** The attachment's id, once uploaded. */
  id?: string;
  error?: string;
};

/** The composer's screenshots: add (pick or paste), remove, and take the
 *  uploaded ones when the message is sent. Switching conversation drops
 *  them: they were uploaded into the one being left. */
export function useScreenshots(conversationId: string, onProblem: (message: string) => void) {
  const [shots, setShots] = useState<PendingShot[]>([]);
  const previews = useRef<string[]>([]);
  const count = useRef(0);
  count.current = shots.length;

  useEffect(() => {
    setShots([]);
    return () => {
      for (const url of previews.current) URL.revokeObjectURL(url);
      previews.current = [];
    };
  }, [conversationId]);

  const add = useCallback(
    (files: File[]) => {
      const room = MAX_IMAGES - count.current;
      if (files.length > room) {
        onProblem(`A message can carry up to ${MAX_IMAGES} screenshots.`);
      }
      for (const file of files.slice(0, Math.max(0, room))) {
        if (!IMAGE_TYPES.includes(file.type)) {
          onProblem("Only images can be attached: PNG, JPEG, WebP or GIF.");
          continue;
        }
        if (file.size > MAX_IMAGE_BYTES) {
          onProblem(
            "This image is larger than 5 MB. Crop it, or save it as a JPEG, and attach it again.",
          );
          continue;
        }
        const key = crypto.randomUUID();
        const preview = URL.createObjectURL(file);
        previews.current.push(preview);
        setShots((s) => [...s, { key, preview, status: "uploading" }]);
        attachmentsApi
          .upload(conversationId, file, file.name || "screenshot.png")
          .then((row) =>
            setShots((s) =>
              s.map((x) => (x.key === key ? { ...x, status: "ready", id: row.id } : x)),
            ),
          )
          .catch((err: unknown) => {
            const error =
              err instanceof ApiError ? err.message : "Couldn't upload this screenshot.";
            setShots((s) => s.map((x) => (x.key === key ? { ...x, status: "failed", error } : x)));
          });
      }
    },
    [conversationId, onProblem],
  );

  const remove = useCallback((key: string) => {
    setShots((s) => s.filter((x) => x.key !== key));
  }, []);

  /** The uploaded ones, for the message being sent; the tray empties. */
  const take = useCallback((): PendingShot[] => {
    const ready = shots.filter((s) => s.status === "ready" && s.id);
    setShots([]);
    return ready;
  }, [shots]);

  /** Put them back: the message was never kept, so they weren't sent. */
  const restore = useCallback((back: PendingShot[]) => {
    setShots((s) => [...back, ...s].slice(0, MAX_IMAGES));
  }, []);

  /** Pasting an image (a screenshot copied to the clipboard) attaches it.
   *  Anything with text in it pastes as text, as before. */
  const onPaste = useCallback(
    (e: ClipboardEvent<HTMLTextAreaElement>) => {
      const data = e.clipboardData;
      if (!data || data.types.includes("text/plain")) return;
      const files = Array.from(data.items)
        .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
        .map((item) => item.getAsFile())
        .filter((f): f is File => f !== null);
      if (files.length === 0) return;
      e.preventDefault();
      add(files);
    },
    [add],
  );

  return {
    shots,
    add,
    remove,
    take,
    restore,
    onPaste,
    uploading: shots.some((s) => s.status === "uploading"),
    ready: shots.filter((s) => s.status === "ready").length,
  };
}

/** The composer's "Attach screenshot" button, with its hidden file picker. */
export function AttachButton({
  onFiles,
  disabled,
}: {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept={IMAGE_TYPES.join(",")}
        multiple
        hidden
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length) onFiles(files);
        }}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="text-muted-foreground hover:text-foreground size-11 shrink-0"
        aria-label="Attach screenshot"
        title="Attach screenshot (or paste one)"
        disabled={disabled}
        onClick={() => input.current?.click()}
      >
        <ImagePlus className="size-5" />
      </Button>
    </>
  );
}

/** Thumbnails of the screenshots waiting to be sent, each with Remove. */
export function ScreenshotTray({
  shots,
  onRemove,
}: {
  shots: PendingShot[];
  onRemove: (key: string) => void;
}) {
  if (shots.length === 0) return null;
  return (
    <ul aria-label="Screenshots to send" className="mb-2 flex flex-wrap gap-2">
      {shots.map((s, i) => (
        <li key={s.key} className="flex flex-col gap-1">
          <div
            className={cn(
              "border-border bg-muted relative size-16 overflow-hidden rounded-md border",
              s.status === "failed" && "border-destructive",
            )}
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- a local blob: preview */}
            <img
              src={s.preview}
              alt={`Screenshot ${i + 1}`}
              className={cn("size-full object-cover", s.status === "uploading" && "opacity-50")}
            />
            <button
              type="button"
              aria-label={`Remove screenshot ${i + 1}`}
              onClick={() => onRemove(s.key)}
              className="bg-background/90 text-foreground hover:bg-background focus-visible:ring-ring absolute top-0.5 right-0.5 rounded-sm p-0.5 focus-visible:ring-2 focus-visible:outline-none"
            >
              <X className="size-3.5" />
            </button>
          </div>
          {s.status === "uploading" && (
            <span className="text-small text-muted-foreground">Uploading…</span>
          )}
          {s.status === "failed" && (
            <span className="text-small text-destructive max-w-40" title={s.error}>
              Not uploaded
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Screenshots in your message bubble: just sent (local previews), or
 *  saved (read back from the server). */
export function MessageImages({
  conversationId,
  ids,
  previews,
}: {
  conversationId: string;
  ids?: string[];
  previews?: string[];
}) {
  if (!ids?.length && !previews?.length) return null;
  return (
    <div className="flex max-w-[85%] flex-wrap justify-end gap-2">
      {previews?.map((src, i) => (
        <Shot key={src} src={src} label={`Screenshot ${i + 1}`} />
      ))}
      {ids?.map((id, i) => (
        <SavedShot key={id} conversationId={conversationId} id={id} n={i + 1} />
      ))}
    </div>
  );
}

function Shot({ src, label }: { src: string; label: string }) {
  return (
    <a href={src} target="_blank" rel="noopener noreferrer" className="block">
      {/* eslint-disable-next-line @next/next/no-img-element -- a blob: address, nothing to optimise */}
      <img
        src={src}
        alt={label}
        className="border-border max-h-48 max-w-full rounded-lg border object-contain"
      />
    </a>
  );
}

/** A saved screenshot: read through the API with your token, into a blob:
 *  address. Pages load images from this site only (next.config.ts), and
 *  reading from the API means object storage needs no cross-origin rules. */
function SavedShot({ conversationId, id, n }: { conversationId: string; id: string; n: number }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let gone = false;
    let made: string | null = null;
    void (async () => {
      try {
        const blob = await attachmentsApi.content(conversationId, id);
        if (gone) return;
        made = URL.createObjectURL(blob);
        setSrc(made);
      } catch {
        if (!gone) setFailed(true);
      }
    })();
    return () => {
      gone = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [conversationId, id]);

  if (failed) {
    return (
      <p className="text-small text-muted-foreground border-border rounded-lg border px-3 py-2">
        Couldn&apos;t load screenshot {n}.
      </p>
    );
  }
  if (!src) {
    return (
      <div
        role="img"
        aria-label={`Screenshot ${n}, loading`}
        className="bg-muted border-border size-24 rounded-lg border"
      />
    );
  }
  return <Shot src={src} label={`Screenshot ${n}`} />;
}
