"use client";

import { Info } from "lucide-react";
import { useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, useConfirm } from "@/components/ui/dialog";
import {
  ApiError,
  chat,
  memories as memoriesApi,
  type AssistantAbout as About,
  type MemoryFile,
} from "@/lib/api";
import { formatRelative } from "@/lib/format";

/** An assistant's card (Phase 8.7): who runs it, when it was approved,
 *  what it looks up and may do, whether it remembers you, and what leaves
 *  the building. From the approved version only, as the server shapes it.
 *  What it remembers about you can be read and forgotten here. */
export function AssistantAbout({ assistantId }: { assistantId: string }) {
  const [open, setOpen] = useState(false);
  const [about, setAbout] = useState<About | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<MemoryFile[] | null>(null);
  const confirm = useConfirm();

  const show = async () => {
    setOpen(true);
    setError(null);
    try {
      setAbout(await chat.about(assistantId));
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Couldn't reach the server. Try again in a moment.",
      );
    }
  };

  const readNotes = async () => {
    try {
      setNotes(await memoriesApi.list(assistantId));
    } catch {
      setError("Couldn't load what it remembers. Try again in a moment.");
    }
  };

  const forget = async () => {
    const sure = await confirm({
      title: "Forget what this assistant remembers about you?",
      description: "Its notes about you are deleted. Your conversations stay.",
      confirmLabel: "Forget all of it",
      destructive: true,
    });
    if (!sure) return;
    try {
      await memoriesApi.clear(assistantId);
      setNotes([]);
    } catch {
      setError("Couldn't forget it. Try again in a moment.");
    }
  };

  return (
    <>
      <Button variant="ghost" size="sm" onClick={() => void show()} aria-haspopup="dialog">
        <Info aria-hidden />
        About
      </Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title={about ? `About ${about.name}` : "About this assistant"}
        description={about?.description || undefined}
        wide
        footer={
          <Button variant="outline" onClick={() => setOpen(false)}>
            Close
          </Button>
        }
      >
        {error && <Alert>{error}</Alert>}
        {about && (
          <div className="flex flex-col gap-4 text-sm">
            <Facts about={about} />
            <Section title="What it uses">
              {about.knowledge.length > 0 && <li>Answers from {list(about.knowledge)}.</li>}
              {about.lookups.map((u) => (
                <li key={u.label}>
                  {u.label}
                  {u.approval ? ", each time after an admin approves" : ""}.
                </li>
              ))}
              {about.knowledge.length === 0 && about.lookups.length === 0 && (
                <li>Nothing beyond the conversation itself.</li>
              )}
            </Section>
            {about.actions.length > 0 && (
              <Section title="What it can do for you">
                {about.actions.map((u) => (
                  <li key={u.label}>
                    {u.label}
                    {u.approval
                      ? ": it asks first, and an admin approves each one"
                      : ": without asking"}
                    .
                  </li>
                ))}
              </Section>
            )}
            <Section title="What it remembers">
              <li>
                {about.keeps_history
                  ? "Each conversation's history, to follow it."
                  : "Not even the conversation: each message starts fresh."}
              </li>
              {about.memory_tool && <li>Notes about you, kept between conversations.</li>}
            </Section>
            {about.memory_tool && (
              <div className="flex flex-col gap-2">
                {notes === null ? (
                  <Button
                    variant="outline"
                    size="sm"
                    className="self-start"
                    onClick={() => void readNotes()}
                  >
                    See what it remembers about you
                  </Button>
                ) : notes.length === 0 ? (
                  <p className="text-muted-foreground">
                    It doesn&apos;t remember anything about you.
                  </p>
                ) : (
                  <>
                    {notes.map((f) => (
                      <pre
                        key={f.path}
                        className="bg-muted border-border text-code max-h-40 overflow-auto rounded-md border p-3 font-mono break-words whitespace-pre-wrap"
                      >
                        {f.content}
                      </pre>
                    ))}
                    <Button
                      variant="outline"
                      size="sm"
                      className="self-start"
                      onClick={() => void forget()}
                    >
                      Forget all of it
                    </Button>
                  </>
                )}
              </div>
            )}
            <Section title="What leaves the building">
              {about.leaves.map((p) => (
                <li key={p.name}>
                  <span className="font-medium">{p.name}:</span> {p.what}
                </li>
              ))}
            </Section>
          </div>
        )}
      </Dialog>
    </>
  );
}

function Facts({ about }: { about: About }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
      {about.owner && (
        <>
          <dt className="text-muted-foreground">Run by</dt>
          <dd>{about.owner}</dd>
        </>
      )}
      <dt className="text-muted-foreground">Version</dt>
      <dd>
        {about.version_number
          ? `${about.version_number}${
              about.approved_at
                ? `, approved ${formatRelative(about.approved_at)}${
                    about.approved_by ? ` by ${about.approved_by}` : ""
                  }`
                : ""
            }`
          : "Not approved yet"}
      </dd>
    </dl>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="text-label mb-1 font-semibold">{title}</h3>
      <ul className="marker:text-muted-foreground list-disc space-y-1 pl-5">{children}</ul>
    </section>
  );
}

function list(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}
