"use client";

import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useId, useState } from "react";

import { ReviewQueue } from "@/components/feedback/ReviewQueue";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { usePrompt } from "@/components/ui/dialog";
import { Loading } from "@/components/ui/loading";
import { SectionHeading } from "@/components/ui/section-heading";
import { ApiError, assistants, type FeedbackSummary } from "@/lib/api";
import { solvedWords, upShare } from "@/lib/feedback";
import { formatRelative } from "@/lib/format";
import { setReadReason } from "@/lib/read-reasons";

/** What people said about an assistant's answers (Phase 8.8), for its
 *  builders and admins: thumbs by version, "Did this solve it?" with how
 *  many answered, and the latest comments with their question and answer.
 *  Never who asked; an admin can open the conversation, which is a
 *  recorded read like any other. Live conversations only. */
export function FeedbackPanel({ assistantId }: { assistantId: string }) {
  const [data, setData] = useState<FeedbackSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const router = useRouter();
  const askReason = usePrompt();

  /** An admin opens the conversation a comment came from: someone else's,
   *  so the reason is asked first and recorded with the read (7b.2). */
  const open = async (conversationId: string) => {
    const reason = (
      await askReason({
        title: "Open this conversation?",
        description:
          "It is someone else's. Your reason is recorded with the read, in the audit log, and they can see it.",
        label: "Why are you opening it?",
        defaultValue: "Following up feedback on an answer",
        confirmLabel: "Open",
      })
    )?.trim();
    if (!reason) return;
    setReadReason(conversationId, reason);
    router.push(`/assistants/${assistantId}/chat?conversation=${conversationId}`);
  };

  useEffect(() => {
    assistants
      .feedback(assistantId)
      .then(setData)
      .catch((err) =>
        setError(
          err instanceof ApiError
            ? err.message
            : "Couldn't reach the server. Check your connection, then reload.",
        ),
      );
  }, [assistantId]);

  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading what="feedback" />;

  return (
    <section aria-labelledby={`${id}-heading`} className="flex max-w-200 flex-col gap-6">
      <SectionHeading
        level={2}
        id={`${id}-heading`}
        title="Feedback"
        description="What people said about its answers in Chat. Comments come with their question and answer, never with who asked. Previews aren't counted."
      />
      <p>{solvedWords(data.solved_yes, data.solved_no, data.conversations)}</p>
      {data.versions.length === 0 ? (
        <p className="text-muted-foreground">No thumbs yet.</p>
      ) : (
        <table className="text-body w-full max-w-120 border-collapse text-left">
          <thead>
            <tr className="border-border border-b">
              <th className="py-1.5 pr-4 font-semibold">Version</th>
              <th className="py-1.5 pr-4 font-semibold">Thumbs up</th>
              <th className="py-1.5 pr-4 font-semibold">Thumbs down</th>
              <th className="py-1.5 pr-4 font-semibold">Up</th>
            </tr>
          </thead>
          <tbody>
            {data.versions.map((v) => {
              const share = upShare(v.up, v.down);
              return (
                <tr key={v.version_number ?? "draft"} className="border-border border-b">
                  <td className="num py-1.5 pr-4">{v.version_number ?? "Draft"}</td>
                  <td className="num py-1.5 pr-4">{v.up}</td>
                  <td className="num py-1.5 pr-4">{v.down}</td>
                  <td className="num py-1.5 pr-4">{share === null ? "–" : `${share}%`}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <ReviewQueue assistantId={assistantId} onOpen={(c) => void open(c)} />
      <div className="flex flex-col gap-3">
        <h3 className="text-h4 font-semibold">Latest comments</h3>
        {data.comments.length === 0 ? (
          <p className="text-muted-foreground">No comments yet.</p>
        ) : (
          <ul className="flex flex-col gap-4">
            {data.comments.map((c, i) => (
              <li key={`${c.created_at}-${i}`} className="border-border rounded-md border p-4">
                <p className="flex items-center gap-2 font-medium">
                  {c.rating === "up" ? (
                    <ThumbsUp aria-label="Thumbs up" className="size-4" />
                  ) : (
                    <ThumbsDown aria-label="Thumbs down" className="size-4" />
                  )}
                  {c.comment}
                </p>
                <p className="text-small text-muted-foreground mt-1">
                  {c.version_number ? `Version ${c.version_number}, ` : ""}
                  {formatRelative(c.created_at)}
                </p>
                <details className="mt-2">
                  <summary className="cursor-pointer text-sm">The question and answer</summary>
                  <p className="text-small text-muted-foreground mt-2">Question</p>
                  <p className="break-words whitespace-pre-wrap">{c.question}</p>
                  <p className="text-small text-muted-foreground mt-2">Answer</p>
                  <p className="break-words whitespace-pre-wrap">{c.answer}</p>
                </details>
                {c.conversation_id && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="mt-2"
                    onClick={() => void open(c.conversation_id as string)}
                  >
                    Open the conversation
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
