"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { SuiteView } from "@/components/evals/SuiteView";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Loading } from "@/components/ui/loading";
import { SectionHeading } from "@/components/ui/section-heading";
import { ApiError, evals, type EvalSuite } from "@/lib/api";
import { RUN_STATUS, isActive, metricsOf, versionLabel } from "@/lib/evals";
import { failureMessage, formatRelative } from "@/lib/format";

const why = (err: unknown) => (err instanceof ApiError ? err.message : null);

/** The Evals tab (task 6.1): suites of questions this assistant must get
 *  right, run against the draft or a published version and scored. */
export function EvalsManager({ assistantId }: { assistantId: string }) {
  const [suites, setSuites] = useState<EvalSuite[] | null>(null);
  const [canEdit, setCanEdit] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await evals.suites(assistantId);
      setSuites(data.suites);
      setCanEdit(data.can_edit);
    } catch (err) {
      setSuites((s) => s ?? []);
      setError(failureMessage("Couldn't load the eval suites.", why(err), "Reload the page."));
    }
  }, [assistantId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function create(ev: FormEvent) {
    ev.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const suite = await evals.createSuite(assistantId, name.trim());
      setName("");
      await load();
      setOpen(suite.id);
    } catch (err) {
      setError(failureMessage("Couldn't create the suite.", why(err), "Try again."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-4 py-6 md:px-8">
        {open ? (
          <SuiteView
            key={open}
            suiteId={open}
            assistantId={assistantId}
            canEdit={canEdit}
            onBack={() => setOpen(null)}
            onChanged={load}
          />
        ) : (
          <>
            <SectionHeading
              level={2}
              title="Evals"
              description="Questions this assistant must get right. Run them before you publish, and compare versions to see what a change fixed or broke."
            />
            {error && <Alert>{error}</Alert>}
            {canEdit && (
              <form onSubmit={create} className="flex flex-wrap items-center gap-2">
                <Input
                  aria-label="New suite name"
                  className="max-w-xs"
                  value={name}
                  maxLength={120}
                  placeholder="Refund questions"
                  onChange={(e) => setName(e.target.value)}
                />
                <Button type="submit" disabled={busy || !name.trim()}>
                  {busy ? "Creating…" : "Create suite"}
                </Button>
              </form>
            )}
            {suites === null ? (
              <Loading what="eval suites" rows={2} rowHeight={56} />
            ) : suites.length === 0 ? (
              <EmptyState
                title="No eval suites yet"
                description={
                  canEdit
                    ? "Name a suite above, then add the questions it should ask. Each run sends them as real messages and scores the answers."
                    : "An editor of this assistant can create one."
                }
              />
            ) : (
              <ul className="border-border border-t">
                {suites.map((s) => (
                  <SuiteRow key={s.id} suite={s} onOpen={() => setOpen(s.id)} />
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function SuiteRow({ suite, onOpen }: { suite: EvalSuite; onOpen: () => void }) {
  const last = suite.last_run;
  const passed = last ? metricsOf(last)[0].text : null;
  return (
    <li className="border-border border-b">
      <button
        type="button"
        onClick={onOpen}
        className="hover:bg-muted/60 focus-visible:ring-ring flex w-full flex-wrap items-center gap-x-4 gap-y-1 rounded-sm px-2 py-3 text-left transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset"
      >
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-medium break-words">{suite.name}</span>
          <span className="num text-small text-muted-foreground block">
            {suite.case_count} {suite.case_count === 1 ? "case" : "cases"}
          </span>
        </span>
        {last ? (
          <>
            <Badge variant={RUN_STATUS[last.status].tone} live={isActive(last)}>
              {RUN_STATUS[last.status].word}
            </Badge>
            <span className="num text-small text-muted-foreground w-64 text-right">
              {passed ? `${passed} passed on ` : ""}
              {versionLabel(last)}, {formatRelative(last.created_at)}
            </span>
          </>
        ) : (
          <span className="text-small text-muted-foreground">Not run yet</span>
        )}
      </button>
    </li>
  );
}
