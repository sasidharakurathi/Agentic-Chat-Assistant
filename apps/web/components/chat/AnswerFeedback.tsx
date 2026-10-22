"use client";

import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { fieldClasses } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export type Rating = "up" | "down";
export type Rated = { rating: Rating | null; comment: string };

/** The longest comment: matches the API's limit. */
const COMMENT_MAX = 1000;

/** A thumbs on one answer, and an optional comment (Phase 8.8). Pressing
 *  the chosen thumbs again takes it back. The comment says where it goes:
 *  to the assistant's team, with this question and answer, without your
 *  name. */
export function AnswerFeedback({
  value,
  onChange,
}: {
  value: Rated | undefined;
  onChange: (next: Rated) => Promise<void>;
}) {
  const rating = value?.rating ?? null;
  const [writing, setWriting] = useState(false);
  const [draft, setDraft] = useState(value?.comment ?? "");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const id = useId();

  const send = async (next: Rated) => {
    setBusy(true);
    setFailed(false);
    try {
      await onChange(next);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const thumb = (which: Rating) => {
    const again = rating === which;
    void send({ rating: again ? null : which, comment: again ? "" : (value?.comment ?? "") });
    if (!again && which === "down" && !value?.comment) setWriting(true);
    if (again) setWriting(false);
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1">
        <Button
          variant="ghost"
          size="icon"
          aria-label="Good answer"
          aria-pressed={rating === "up"}
          disabled={busy}
          className={cn("size-8", rating === "up" && "bg-muted text-foreground")}
          onClick={() => thumb("up")}
        >
          <ThumbsUp aria-hidden className="size-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Bad answer"
          aria-pressed={rating === "down"}
          disabled={busy}
          className={cn("size-8", rating === "down" && "bg-muted text-foreground")}
          onClick={() => thumb("down")}
        >
          <ThumbsDown aria-hidden className="size-4" />
        </Button>
        {rating && !writing && (
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground"
            onClick={() => setWriting(true)}
          >
            {value?.comment ? "Edit your comment" : "Add a comment"}
          </Button>
        )}
        <span role="status" className="text-small text-muted-foreground">
          {failed ? "Couldn't save that. Try again." : rating && !writing ? "Thanks." : ""}
        </span>
      </div>
      {rating && value?.comment && !writing && (
        <p className="text-small text-muted-foreground max-w-[68ch]">
          Your comment: {value.comment}
        </p>
      )}
      {rating && writing && (
        <form
          className="flex max-w-[68ch] flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void send({ rating, comment: draft }).then(() => setWriting(false));
          }}
        >
          <label htmlFor={`${id}-comment`} className="text-small">
            What was wrong, or right? (optional)
          </label>
          <textarea
            id={`${id}-comment`}
            aria-describedby={`${id}-who`}
            rows={2}
            maxLength={COMMENT_MAX}
            className={cn(fieldClasses, "min-h-16 resize-y px-3 py-2")}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <p id={`${id}-who`} className="text-small text-muted-foreground">
            Shared with the assistant&apos;s team, with this question and answer, without your name.
          </p>
          <div className="flex gap-2">
            <Button size="sm" type="submit" disabled={busy}>
              Send
            </Button>
            <Button size="sm" variant="ghost" type="button" onClick={() => setWriting(false)}>
              Not now
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

/** "Did this solve it?" (Phase 8.8): once per conversation, under its
 *  latest answer. Changeable; kept without your name. */
export function SolvedPrompt({
  value,
  onChange,
}: {
  value: boolean | null;
  onChange: (solved: boolean) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [changing, setChanging] = useState(false);

  const choose = async (solved: boolean) => {
    setBusy(true);
    setFailed(false);
    try {
      await onChange(solved);
      setChanging(false);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  if (value !== null && !changing) {
    return (
      <p
        role="status"
        className="text-small text-muted-foreground flex flex-wrap items-center gap-2"
      >
        {value
          ? "You said this solved it. Thanks."
          : "You said this didn't solve it. Thanks for saying."}
        <Button variant="ghost" size="sm" onClick={() => setChanging(true)}>
          Change
        </Button>
      </p>
    );
  }
  return (
    <div role="group" aria-label="Did this solve it?" className="flex flex-wrap items-center gap-2">
      <span className="text-small">Did this solve it?</span>
      <Button size="sm" variant="outline" disabled={busy} onClick={() => void choose(true)}>
        Yes
      </Button>
      <Button size="sm" variant="outline" disabled={busy} onClick={() => void choose(false)}>
        No
      </Button>
      {failed && (
        <span className="text-small text-destructive">Couldn&apos;t save that. Try again.</span>
      )}
    </div>
  );
}
