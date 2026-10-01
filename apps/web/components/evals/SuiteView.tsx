"use client";

import { ChevronLeft, Upload } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState, type ChangeEvent } from "react";

import { Toggle } from "@/components/config/panels";
import { CaseForm } from "@/components/evals/CaseForm";
import { RunCompare, RunView } from "@/components/evals/RunView";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useConfirm, usePrompt } from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Label } from "@/components/ui/label";
import { Loading } from "@/components/ui/loading";
import { SectionHeading } from "@/components/ui/section-heading";
import { Select } from "@/components/ui/select";
import { useToast } from "@/components/ui/toast";
import {
  ApiError,
  assistants,
  evals,
  type AssistantVersion,
  type EvalCase,
  type EvalCaseDraft,
  type EvalRun,
  type EvalRunDetail,
  type EvalSuiteDetail,
} from "@/lib/api";
import {
  RUN_STATUS,
  expectations,
  isActive,
  metricsOf,
  parseCasesFile,
  versionLabel,
} from "@/lib/evals";
import { failureMessage, formatRelative, formatUsd } from "@/lib/format";

const POLL_MS = 2000;
const DRAFT = "draft";

const why = (err: unknown) => (err instanceof ApiError ? err.message : null);

/** One suite (task 6.1): its cases, a run against a chosen version, and the
 *  runs so far, with any two compared. */
export function SuiteView({
  suiteId,
  assistantId,
  canEdit,
  onBack,
  onChanged,
}: {
  suiteId: string;
  assistantId: string;
  canEdit: boolean;
  onBack: () => void;
  /** The suite's name, cases or latest run changed: the list is stale. */
  onChanged: () => void;
}) {
  const [suite, setSuite] = useState<EvalSuiteDetail | null>(null);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [versions, setVersions] = useState<AssistantVersion[]>([]);
  const [target, setTarget] = useState(DRAFT);
  const [shown, setShown] = useState<EvalRunDetail | null>(null);
  const [baseline, setBaseline] = useState<EvalRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<{ editing: EvalCase | null } | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const confirm = useConfirm();
  const prompt = usePrompt();
  const toast = useToast();
  const ids = useId();

  const load = useCallback(async () => {
    try {
      const [detail, list] = await Promise.all([evals.suite(suiteId), evals.runs(suiteId)]);
      setSuite(detail);
      setRuns(list.runs);
      return list.runs;
    } catch (err) {
      setError(
        failureMessage("Couldn't load this suite.", why(err), "Reload the page to try again."),
      );
      return [];
    }
  }, [suiteId]);

  const show = useCallback(async (runId: string) => {
    try {
      setShown(await evals.run(runId));
    } catch (err) {
      setError(failureMessage("Couldn't load that run.", why(err), "Pick it again to retry."));
    }
  }, []);

  useEffect(() => {
    void load().then((list) => {
      if (list[0]) void show(list[0].id);
    });
    assistants
      .versions(assistantId)
      .then((page) => setVersions(page.items))
      .catch(() => setVersions([]));
  }, [assistantId, load, show]);

  // While a run is going, follow it: its progress here, its results below.
  const active = runs.find(isActive) ?? null;
  const activeId = active?.id ?? null;
  useEffect(() => {
    if (!activeId) return;
    const timer = setInterval(() => {
      void load().then((list) => {
        void show(activeId);
        if (!list.some(isActive)) onChanged();
      });
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [activeId, load, show, onChanged]);

  async function act(what: string, todo: string, work: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(failureMessage(what, why(err), todo));
    } finally {
      setBusy(false);
    }
  }

  const rename = async () => {
    if (!suite) return;
    const name = await prompt({
      title: "Rename this suite",
      label: "Name",
      defaultValue: suite.name,
      confirmLabel: "Rename suite",
    });
    if (!name?.trim() || name.trim() === suite.name) return;
    await act("Couldn't rename the suite.", "Try again.", async () => {
      setSuite(await evals.updateSuite(suiteId, { name: name.trim() }));
      onChanged();
    });
  };

  const remove = async () => {
    if (!suite) return;
    const sure = await confirm({
      title: `Delete "${suite.name}"?`,
      description: "Its cases and every run of it are deleted. This can't be undone.",
      confirmLabel: "Delete suite",
      destructive: true,
    });
    if (!sure) return;
    await act("Couldn't delete the suite.", "Try again.", async () => {
      await evals.removeSuite(suiteId);
      toast(`Deleted "${suite.name}"`, "success");
      onChanged();
      onBack();
    });
  };

  const setJudge = (judge: boolean) =>
    suite &&
    act("Couldn't change the suite.", "Try again.", async () => {
      setSuite(await evals.updateSuite(suiteId, { config: { ...suite.config, judge } }));
    });

  const saveCase = (draft: EvalCaseDraft) =>
    act("Couldn't save the case.", "Check it and try again.", async () => {
      if (form?.editing) await evals.updateCase(suiteId, form.editing.id, draft);
      else await evals.addCases(suiteId, [draft]);
      setForm(null);
      await load();
      onChanged();
    });

  const removeCase = async (c: EvalCase) => {
    const sure = await confirm({
      title: "Delete this case?",
      description: `"${c.input}" is removed from the suite. Results of earlier runs keep it.`,
      confirmLabel: "Delete case",
      destructive: true,
    });
    if (!sure) return;
    await act("Couldn't delete the case.", "Try again.", async () => {
      await evals.removeCase(suiteId, c.id);
      await load();
      onChanged();
    });
  };

  const importFile = async (ev: ChangeEvent<HTMLInputElement>) => {
    const picked = ev.target.files?.[0];
    ev.target.value = ""; // the same file can be picked again
    if (!picked) return;
    const { cases, problems } = parseCasesFile(await picked.text(), picked.name);
    setNotes(problems);
    if (cases.length === 0) {
      setError(`No cases could be read from ${picked.name}. Check it against the format below.`);
      return;
    }
    await act(`Couldn't import ${picked.name}.`, "Fix the file and import it again.", async () => {
      await evals.addCases(suiteId, cases);
      toast(`Added ${cases.length} ${cases.length === 1 ? "case" : "cases"}`, "success");
      await load();
      onChanged();
    });
  };

  const start = () =>
    act("Couldn't start the run.", "Try again in a moment.", async () => {
      const run = await evals.start(suiteId, target === DRAFT ? null : target);
      setBaseline(null);
      await load();
      await show(run.id);
      onChanged();
    });

  const cancel = () =>
    active &&
    act("Couldn't cancel the run.", "Try again.", async () => {
      await evals.cancel(active.id);
      await load();
      onChanged();
    });

  const compare = async (runId: string) => {
    if (!runId) return setBaseline(null);
    await act("Couldn't load the run to compare with.", "Pick it again.", async () => {
      setBaseline(await evals.run(runId));
    });
  };

  if (!suite) {
    return error ? <Alert>{error}</Alert> : <Loading what="the suite" rows={3} rowHeight={52} />;
  }
  const others = runs.filter((r) => r.id !== shown?.id && !isActive(r));

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <Button variant="ghost" size="sm" className="-ml-2 self-start" onClick={onBack}>
          <ChevronLeft aria-hidden className="size-4" />
          All suites
        </Button>
        <SectionHeading
          level={2}
          title={suite.name}
          description="Each case is asked as a real message, then scored."
          actions={
            canEdit && (
              <>
                <Button variant="outline" size="sm" disabled={busy} onClick={() => void rename()}>
                  Rename
                </Button>
                <Button variant="outline" size="sm" disabled={busy} onClick={() => void remove()}>
                  Delete suite
                </Button>
              </>
            )
          }
        />
        {error && <Alert>{error}</Alert>}
        {notes.length > 0 && (
          <Alert tone="warning" title="Some rows were left out">
            <ul className="list-disc pl-4">
              {notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </Alert>
        )}
      </div>

      <section className="flex flex-col gap-3" aria-labelledby={`${ids}-cases`}>
        <SectionHeading
          id={`${ids}-cases`}
          level={3}
          title={`Cases (${suite.cases.length})`}
          actions={
            canEdit && (
              <>
                <input
                  ref={file}
                  type="file"
                  accept=".csv,.json,text/csv,application/json"
                  className="sr-only"
                  aria-label="Import cases from a CSV or JSON file"
                  tabIndex={-1}
                  onChange={(ev) => void importFile(ev)}
                />
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => file.current?.click()}
                >
                  <Upload aria-hidden className="size-4" />
                  Import a file
                </Button>
                <Button size="sm" disabled={busy} onClick={() => setForm({ editing: null })}>
                  Add a case
                </Button>
              </>
            )
          }
        />
        {suite.cases.length === 0 ? (
          <EmptyState
            headingLevel={4}
            title="No cases yet"
            description="Add the questions this assistant must get right. Import a CSV with an input column (and optionally reference, contains, not_contains, tools, cites, refuses, relevant_sources; lists separated by |), or a JSON list of cases."
          />
        ) : (
          <ol className="border-border border-t">
            {suite.cases.map((c, n) => {
              const asks = expectations(c.expected, c.labels);
              return (
                <li key={c.id} className="border-border flex items-start gap-3 border-b py-2.5">
                  <span className="num text-small text-muted-foreground w-6 shrink-0 pt-0.5 text-right">
                    {n + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium break-words">{c.input}</p>
                    <p className="text-small text-muted-foreground break-words">
                      {asks.length > 0 ? asks.join(". ") + "." : "Passes if the assistant answers."}
                    </p>
                  </div>
                  {canEdit && (
                    <div className="flex shrink-0 gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        aria-label={`Change case ${n + 1}`}
                        onClick={() => setForm({ editing: c })}
                      >
                        Change
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        aria-label={`Delete case ${n + 1}`}
                        onClick={() => void removeCase(c)}
                      >
                        Delete
                      </Button>
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </section>

      <section className="flex flex-col gap-3" aria-labelledby={`${ids}-run`}>
        <SectionHeading id={`${ids}-run`} level={3} title="Run the suite" />
        <Card className="flex flex-col gap-4 p-4">
          {canEdit && (
            <Toggle
              label="Grade answers with the judge"
              hint={
                suite.judge_available
                  ? "A second model scores each answer from 1 to 5 for being grounded, correct, well cited and declining only when it should. It costs a model call per case."
                  : "The judge needs the real model, which is switched off on this server. Runs use the free checks only."
              }
              checked={suite.config.judge}
              disabled={busy}
              onChange={(v) => void setJudge(v)}
            />
          )}
          {active ? (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p role="status" className="num flex items-center gap-2 text-sm">
                <Badge variant="warning" live>
                  {RUN_STATUS[active.status].word}
                </Badge>
                {active.done_cases} of {active.total_cases} cases on {versionLabel(active)}
              </p>
              {canEdit && (
                <Button variant="outline" size="sm" disabled={busy} onClick={() => void cancel()}>
                  Cancel run
                </Button>
              )}
            </div>
          ) : canEdit ? (
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex min-w-48 flex-col gap-1.5">
                <Label htmlFor={`${ids}-target`}>Run against</Label>
                <Select
                  id={`${ids}-target`}
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                >
                  <option value={DRAFT}>The draft</option>
                  {versions.map((v) => (
                    <option key={v.id} value={v.id}>
                      Version {v.version_number}
                      {v.note ? `: ${v.note.slice(0, 40)}` : ""}
                    </option>
                  ))}
                </Select>
              </div>
              <Button disabled={busy || suite.cases.length === 0} onClick={() => void start()}>
                Run {suite.cases.length} {suite.cases.length === 1 ? "case" : "cases"}
              </Button>
              {suite.cases.length === 0 && (
                <p className="text-small text-muted-foreground">Add a case to run the suite.</p>
              )}
            </div>
          ) : (
            <p className="text-muted-foreground text-sm">
              Only this assistant&apos;s editors can run its evals.
            </p>
          )}
        </Card>
      </section>

      {runs.length > 0 && (
        <section className="flex flex-col gap-3" aria-labelledby={`${ids}-runs`}>
          <SectionHeading id={`${ids}-runs`} level={3} title="Runs" />
          <ul className="border-border border-t">
            {runs.map((r) => {
              const status = RUN_STATUS[r.status];
              const passed = metricsOf(r)[0].text;
              return (
                <li key={r.id} className="border-border border-b">
                  <button
                    type="button"
                    aria-pressed={shown?.id === r.id}
                    onClick={() => {
                      setBaseline(null);
                      void show(r.id);
                    }}
                    className="hover:bg-muted/60 aria-pressed:bg-muted focus-visible:ring-ring flex w-full flex-wrap items-center gap-x-4 gap-y-1 rounded-sm px-2 py-2.5 text-left text-sm transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset"
                  >
                    <Badge
                      variant={status.tone}
                      live={isActive(r)}
                      className="w-32 shrink-0 justify-start"
                    >
                      {status.word}
                    </Badge>
                    <span className="num w-24 shrink-0 font-medium">{versionLabel(r)}</span>
                    <span className="num min-w-0 flex-1">
                      {passed ? `${passed} passed` : `${r.done_cases} of ${r.total_cases} cases`}
                    </span>
                    <span className="num text-muted-foreground">{formatUsd(r.cost_usd)}</span>
                    <span className="text-small text-muted-foreground w-28 text-right">
                      {formatRelative(r.created_at)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {shown && (
        <section className="flex flex-col gap-4" aria-labelledby={`${ids}-results`}>
          <SectionHeading
            id={`${ids}-results`}
            level={3}
            title={`Results on ${versionLabel(shown).toLowerCase() === "draft" ? "the draft" : versionLabel(shown)}`}
            description={`Started ${formatRelative(shown.created_at)}.`}
            actions={
              others.length > 0 && (
                <div className="flex items-center gap-2">
                  <Label htmlFor={`${ids}-baseline`}>Compare with</Label>
                  <Select
                    id={`${ids}-baseline`}
                    className="w-56"
                    value={baseline?.id ?? ""}
                    onChange={(e) => void compare(e.target.value)}
                  >
                    <option value="">Nothing</option>
                    {others.map((r) => (
                      <option key={r.id} value={r.id}>
                        {versionLabel(r)}, {formatRelative(r.created_at)}
                      </option>
                    ))}
                  </Select>
                </div>
              )
            }
          />
          {baseline ? <RunCompare before={baseline} after={shown} /> : <RunView run={shown} />}
        </section>
      )}

      {form && (
        <CaseForm
          key={form.editing?.id ?? "new"}
          open
          editing={form.editing}
          busy={busy}
          onClose={() => setForm(null)}
          onSave={(draft) => void saveCase(draft)}
        />
      )}
    </div>
  );
}
