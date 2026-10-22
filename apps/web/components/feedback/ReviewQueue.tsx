"use client";

import { useCallback, useEffect, useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { usePrompt } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Loading } from "@/components/ui/loading";
import { SectionHeading } from "@/components/ui/section-heading";
import { Segmented } from "@/components/ui/segmented";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import {
  ApiError,
  evals,
  reviewQueue,
  type EvalSuite,
  type ReviewItem,
  type ReviewReason,
  type ReviewState,
} from "@/lib/api";
import { formatRelative } from "@/lib/format";

const REASONS: Record<ReviewReason, string> = {
  thumbs_down: "Thumbs down",
  not_solved: "Didn't solve it",
  failed: "The answer failed",
  declined: "It declined to answer",
};

const STATES: { value: ReviewState; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "turned_into_case", label: "Turned into test cases" },
  { value: "dismissed", label: "Dismissed" },
];

const EMPTY: Record<ReviewState, string> = {
  open: "Nothing needs review. Answers people give a thumbs down, or say didn't solve their problem, land here.",
  turned_into_case: "No test cases made from Chat yet.",
  dismissed: "Nothing dismissed.",
};

function failed(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/** The needs-review queue (Phase 10.3), part of the Feedback tab: answers
 *  people complained about (a failed turn is noted on those), to turn into
 *  test cases or dismiss. When a test case passes on the live version,
 *  whoever asked is emailed once. Never who asked; an admin can open the conversation, a
 *  recorded read like any other. */
export function ReviewQueue({
  assistantId,
  onOpen,
}: {
  assistantId: string;
  /** Admins: open the conversation an item came from. */
  onOpen: (conversationId: string) => void;
}) {
  const id = useId();
  const [state, setState] = useState<ReviewState>("open");
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [suites, setSuites] = useState<EvalSuite[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [making, setMaking] = useState<string | null>(null);
  const askReason = usePrompt();
  const toast = useToast();

  const load = useCallback(async () => {
    setError(null);
    try {
      setItems(await reviewQueue.list(assistantId, state));
    } catch (err) {
      setItems([]);
      setError(failed(err, "Couldn't load the queue. Check your connection, then reload."));
    }
  }, [assistantId, state]);

  useEffect(() => {
    setItems(null);
    void load();
  }, [load]);

  useEffect(() => {
    evals
      .suites(assistantId)
      .then((r) => setSuites(r.suites))
      .catch(() => setSuites([]));
  }, [assistantId]);

  const suiteName = (suiteId?: string | null) =>
    suites.find((s) => s.id === suiteId)?.name ?? "a suite";

  const dismiss = async (item: ReviewItem) => {
    const reason = (
      await askReason({
        title: "Dismiss this answer?",
        description: "It leaves the queue. Your reason stays with it, for the rest of the team.",
        label: "Why is there nothing to fix?",
        confirmLabel: "Dismiss",
      })
    )?.trim();
    if (!reason) return;
    try {
      await reviewQueue.dismiss(assistantId, item.id, reason);
      toast("Dismissed", "success");
      await load();
    } catch (err) {
      toast(failed(err, "Couldn't dismiss it. Try again."), "error");
    }
  };

  return (
    <section aria-labelledby={`${id}-heading`} className="flex flex-col gap-4">
      <SectionHeading
        level={3}
        id={`${id}-heading`}
        title="Needs review"
        description="Answers people gave a thumbs down or said didn't solve their problem. Turn one into a test case: when it passes on the live version, whoever asked is emailed that it was fixed. You never see who asked."
      />
      <Segmented
        label="Which answers"
        value={state}
        onChange={setState}
        options={STATES}
        size="sm"
        className="self-start"
      />
      {error && <Alert>{error}</Alert>}
      {items === null ? (
        <Loading what="the queue" />
      ) : items.length === 0 ? (
        <p className="text-muted-foreground">{EMPTY[state]}</p>
      ) : (
        <ul className="flex flex-col gap-4">
          {items.map((item) => (
            <li key={item.id} className="border-border rounded-md border p-4">
              <div className="flex flex-wrap items-center gap-2">
                {item.reasons.map((r) => (
                  <Badge key={r} variant={r === "thumbs_down" ? "warning" : "destructive"}>
                    {REASONS[r]}
                  </Badge>
                ))}
                <span className="text-small text-muted-foreground">
                  {item.version_number ? `Version ${item.version_number}, ` : ""}
                  {formatRelative(item.created_at)}
                </span>
              </div>
              {item.comment && <p className="mt-2 font-medium">{item.comment}</p>}
              <p className="text-small text-muted-foreground mt-2">Question</p>
              <p className="break-words whitespace-pre-wrap">{item.question || "(none)"}</p>
              <details className="mt-2">
                <summary className="cursor-pointer text-sm">The answer</summary>
                <p className="mt-2 break-words whitespace-pre-wrap">{item.answer}</p>
              </details>

              {item.state === "turned_into_case" && (
                <p className="text-small mt-2">
                  {item.eval_case_id
                    ? `A test case in ${suiteName(item.suite_id)}. `
                    : "Its test case was deleted. "}
                  {item.fixed_version_number
                    ? item.asker_told
                      ? `Fixed in version ${item.fixed_version_number}; whoever asked was emailed.`
                      : `Fixed in version ${item.fixed_version_number}.`
                    : item.eval_case_id
                      ? "Whoever asked is emailed once it passes on the live version."
                      : ""}
                </p>
              )}
              {item.state === "dismissed" && item.dismissed_reason && (
                <p className="text-small mt-2">Dismissed: {item.dismissed_reason}</p>
              )}

              {making === item.id ? (
                <CaseForm
                  assistantId={assistantId}
                  item={item}
                  suites={suites}
                  onSuites={setSuites}
                  onCancel={() => setMaking(null)}
                  onDone={async () => {
                    setMaking(null);
                    toast("Added to the suite", "success");
                    await load();
                  }}
                />
              ) : (
                <div className="mt-3 flex flex-wrap gap-2">
                  {(item.state === "open" ||
                    (item.state === "dismissed" && !item.eval_case_id)) && (
                    <Button size="sm" variant="outline" onClick={() => setMaking(item.id)}>
                      Turn into a test case
                    </Button>
                  )}
                  {item.state === "open" && (
                    <Button size="sm" variant="ghost" onClick={() => void dismiss(item)}>
                      Dismiss
                    </Button>
                  )}
                  {item.conversation_id && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onOpen(item.conversation_id as string)}
                    >
                      Open the conversation
                    </Button>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** What the test case asks, and what a good answer looks like. */
function CaseForm({
  assistantId,
  item,
  suites,
  onSuites,
  onCancel,
  onDone,
}: {
  assistantId: string;
  item: ReviewItem;
  suites: EvalSuite[];
  onSuites: (s: EvalSuite[]) => void;
  onCancel: () => void;
  onDone: () => Promise<void>;
}) {
  const id = useId();
  const [suiteId, setSuiteId] = useState(suites[0]?.id ?? "");
  const [question, setQuestion] = useState(item.question);
  const [includes, setIncludes] = useState("");
  const [reference, setReference] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const newSuite = async () => {
    setBusy(true);
    setError(null);
    try {
      const made = await evals.createSuite(assistantId, "From Chat");
      onSuites([...suites, made]);
      setSuiteId(made.id);
    } catch (err) {
      setError(failed(err, "Couldn't create the suite. Try again."));
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await reviewQueue.toCase(assistantId, item.id, {
        suite_id: suiteId,
        input: question.trim(),
        expected: {
          contains: includes
            .split(",")
            .map((w) => w.trim())
            .filter(Boolean),
          reference: reference.trim() || null,
        },
      });
      await onDone();
    } catch (err) {
      setError(failed(err, "Couldn't add the test case. Try again."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="border-border mt-3 flex flex-col gap-3 border-t pt-3"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      {suites.length === 0 ? (
        <div className="flex flex-col items-start gap-2">
          <p className="text-small text-muted-foreground">
            This assistant has no eval suites yet. Test cases live in one.
          </p>
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={newSuite}>
            Create a suite called From Chat
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-suite`}>Suite</Label>
          <Select id={`${id}-suite`} value={suiteId} onChange={(e) => setSuiteId(e.target.value)}>
            {suites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-question`}>Question the test asks</Label>
        <Textarea
          id={`${id}-question`}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={3}
        />
        <p className="text-small text-muted-foreground">
          The person&apos;s own words. Take out anything personal before you save it.
        </p>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-includes`}>A good answer includes (comma-separated, optional)</Label>
        <Input
          id={`${id}-includes`}
          value={includes}
          onChange={(e) => setIncludes(e.target.value)}
          placeholder="Acme-Guest, reception"
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-reference`}>A reference answer (optional)</Label>
        <Textarea
          id={`${id}-reference`}
          value={reference}
          onChange={(e) => setReference(e.target.value)}
          rows={3}
        />
      </div>
      {error && <Alert>{error}</Alert>}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy || !suiteId || !question.trim()}>
          Add to the suite
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
