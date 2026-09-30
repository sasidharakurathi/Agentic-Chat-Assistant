"use client";

import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";

import { AgentPanel, GuardrailsPanel, MemoryPanel } from "@/components/config/panels";
import { PipelineRecommend } from "@/components/config/PipelineRecommend";
import { LoadFailed, loadFailure, type LoadFailure } from "@/components/load-state";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Loading } from "@/components/ui/loading";
import { PageHeader } from "@/components/ui/page-header";
import {
  ApiError,
  assist,
  assistants,
  meta,
  type AssistantConfig,
  type PipelineSuggestion,
} from "@/lib/api";
import { failureMessage } from "@/lib/format";
import { cn } from "@/lib/utils";

type Data = Record<string, unknown>;

const STEPS = ["Basics", "Pipeline", "Agent", "Guardrails", "Memory", "Review"] as const;
type Step = (typeof STEPS)[number];

const STEP_DESCRIPTION: Record<Step, string> = {
  Basics: "What is this assistant called?",
  Pipeline:
    "Optional. Describe the assistant to get a starter pipeline to look over: a knowledge base, tools, a system prompt and rules. Or press Next to start with the basic one.",
  Agent: "Describe what it should do, which becomes its system prompt, and pick a model.",
  Guardrails: "Optional rules and safety defaults.",
  Memory: "How it handles long conversations.",
  Review: "You can change all of this later in the builder.",
};

const EFFORT_LABEL: Record<string, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Maximum",
};

function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`;
}

export default function NewAssistantWizard() {
  const router = useRouter();
  const ids = useId();
  const [ready, setReady] = useState(false);
  const [failure, setFailure] = useState<LoadFailure | null>(null);
  const [models, setModels] = useState<string[]>([]);

  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [agentData, setAgentData] = useState<Data>({});
  const [guardrailsData, setGuardrailsData] = useState<Data>({});
  const [memoryData, setMemoryData] = useState<Data>({});
  // The recommended starter pipeline, if the builder took one.
  const [starter, setStarter] = useState<PipelineSuggestion | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Move focus to the step's heading when the step changes (not on first
  // render, where the Name field takes focus).
  const headingRef = useRef<HTMLHeadingElement>(null);
  const firstStep = useRef(true);
  useEffect(() => {
    if (firstStep.current) {
      firstStep.current = false;
      return;
    }
    headingRef.current?.focus();
  }, [step]);

  useEffect(() => {
    meta
      .configSchema()
      .then((res) => {
        const def = res.default;
        setAgentData({ system_prompt: def.system_prompt, models: def.models });
        setGuardrailsData(def.guardrails as Data);
        setMemoryData(def.memory as Data);
        setModels(res.allowed_models);
        setReady(true);
      })
      .catch((err: unknown) => setFailure(loadFailure(err)));
  }, []);

  function patch(setter: Dispatch<SetStateAction<Data>>) {
    return (fields: Data) => setter((prev) => ({ ...prev, ...fields }));
  }

  const goTo = useCallback((i: number) => {
    setStep(Math.max(0, Math.min(STEPS.length - 1, i)));
  }, []);

  const current: Step = STEPS[step];
  const canAdvance = current !== "Basics" || name.trim().length > 0;

  async function create() {
    setCreating(true);
    setError(null);
    try {
      const created = await assistants.create(name.trim(), description.trim());
      // The starter pipeline, if one was taken, with the later steps' edits
      // on top. Saving the config lays out the canvas.
      const base = starter?.config ?? created.draft_config;
      const merged: AssistantConfig = {
        ...base,
        system_prompt: (agentData.system_prompt as string) ?? base.system_prompt,
        models: (agentData.models as AssistantConfig["models"]) ?? base.models,
        guardrails: { ...base.guardrails, ...guardrailsData },
        memory: { ...(base.memory as Data), ...memoryData },
      };
      await assistants.putDraftConfig(created.id, merged);
      router.push(`/assistants/${created.id}/build`);
    } catch (err) {
      setError(
        failureMessage(
          "Couldn't create the assistant.",
          err instanceof ApiError ? err.message : null,
          "Check your connection, then press Create assistant again.",
        ),
      );
      setCreating(false);
    }
  }

  if (failure) return <LoadFailed failure={failure} what="guided setup" />;

  const header = (
    <PageHeader
      back={{ href: "/assistants", label: "Assistants" }}
      title="Guided setup"
      description="A few questions, then you land on the canvas with a starter pipeline wired up."
    />
  );

  if (!ready) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-8 md:px-6 lg:px-8">
        {header}
        <Loading what="guided setup" />
      </div>
    );
  }

  const main = ((agentData.models as Data)?.main as Data) ?? {};
  const rules = (guardrailsData.rules as string[]) ?? [];
  const pipeline = starter
    ? starter.capabilities.map((c) => c.label).join(", ") || "Conversation only"
    : "Basic: no knowledge base or tools yet";
  const model = String(main.model ?? "");
  const effort = String(main.effort ?? "");

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 md:px-6 lg:px-8">
      {header}

      <StepLine current={step} onPick={goTo} />

      <section aria-labelledby={`${ids}-step`} className="mt-8 flex max-w-160 flex-col gap-6">
        <div className="flex flex-col gap-1">
          <p className="text-small text-muted-foreground num">
            Step {step + 1} of {STEPS.length}
          </p>
          <h2
            ref={headingRef}
            id={`${ids}-step`}
            tabIndex={-1}
            className="text-h2 font-semibold focus-visible:outline-none"
          >
            {current}
          </h2>
          <p className="text-muted-foreground max-w-[60ch]">{STEP_DESCRIPTION[current]}</p>
        </div>

        {current === "Basics" && (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${ids}-name`}>Name</Label>
              <Input
                id={`${ids}-name`}
                autoFocus
                required
                autoComplete="off"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Support triage bot"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${ids}-description`}>Description</Label>
              <Input
                id={`${ids}-description`}
                autoComplete="off"
                aria-describedby={`${ids}-description-hint`}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Answers billing questions from the help docs"
              />
              <p id={`${ids}-description-hint`} className="text-small text-muted-foreground">
                Optional. Shown in the assistant list.
              </p>
            </div>
          </div>
        )}

        {current === "Pipeline" &&
          (starter ? (
            <div className="flex flex-col items-start gap-3">
              <p>
                Using the suggested pipeline:{" "}
                {starter.capabilities.map((c) => c.label).join(", ") || "conversation only"}. The
                next steps show its prompt, rules and memory settings for you to adjust.
              </p>
              <Button size="sm" variant="outline" onClick={() => setStarter(null)}>
                Choose again
              </Button>
            </div>
          ) : (
            <PipelineRecommend
              initialDescription={description}
              request={(d) => assist.recommendStarter({ description: d, name: name.trim() })}
              applyLabel="Use this pipeline"
              onApply={(s) => {
                setStarter(s);
                setAgentData({ system_prompt: s.config.system_prompt, models: s.config.models });
                setGuardrailsData(s.config.guardrails as Data);
                setMemoryData(s.config.memory as Data);
                setStep((i) => i + 1);
              }}
            />
          ))}

        {current === "Agent" && (
          <AgentPanel data={agentData} models={models} onChange={patch(setAgentData)} />
        )}

        {current === "Guardrails" && (
          <GuardrailsPanel data={guardrailsData} onChange={patch(setGuardrailsData)} />
        )}

        {current === "Memory" && <MemoryPanel data={memoryData} onChange={patch(setMemoryData)} />}

        {current === "Review" && (
          <div className="flex flex-col gap-4">
            <dl className="border-border border-t">
              <ReviewRow term="Name">{name}</ReviewRow>
              {description && <ReviewRow term="Description">{description}</ReviewRow>}
              <ReviewRow term="Pipeline">{pipeline}</ReviewRow>
              <ReviewRow term="Model">{model || "Not set"}</ReviewRow>
              <ReviewRow term="Effort">{EFFORT_LABEL[effort] ?? (effort || "Not set")}</ReviewRow>
              <ReviewRow term="System prompt">
                <span className="text-muted-foreground line-clamp-3">
                  {String(agentData.system_prompt ?? "") || "Not set"}
                </span>
              </ReviewRow>
              <ReviewRow term="Guardrail rules">
                <span className="num">{plural(rules.length, "rule", "rules")}</span>
              </ReviewRow>
            </dl>
          </div>
        )}

        <div className="border-border flex flex-col gap-4 border-t pt-6">
          {error && <Alert>{error}</Alert>}
          <div className="flex justify-between gap-3">
            <Button variant="outline" disabled={step === 0} onClick={() => goTo(step - 1)}>
              Back
            </Button>
            {current === "Review" ? (
              <Button onClick={create} disabled={creating}>
                {creating ? "Creating…" : "Create assistant"}
              </Button>
            ) : (
              <Button disabled={!canAdvance} onClick={() => goTo(step + 1)}>
                Next
              </Button>
            )}
          </div>
          {current === "Basics" && !canAdvance && (
            <p className="text-small text-muted-foreground">Enter a name to continue.</p>
          )}
        </div>
      </section>
    </div>
  );
}

/** One labelled line of the review. */
function ReviewRow({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="border-border grid gap-1 border-b py-3 sm:grid-cols-[10rem_minmax(0,1fr)] sm:gap-4">
      <dt className="text-muted-foreground">{term}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

/** The steps as the main line (docs/DESIGN.md section 1): numbered bullets
 *  on one ink line, since the steps really are a sequence. Done steps are
 *  ink and can be revisited; the current one wears the yellow marker;
 *  later ones are hollow on an unlit line. Labels show from 640px up; the
 *  step heading below says where you are on a phone. */
function StepLine({ current, onPick }: { current: number; onPick: (i: number) => void }) {
  return (
    <ol aria-label="Setup steps" className="flex w-full max-w-160">
      {STEPS.map((s, i) => {
        const done = i < current;
        const here = i === current;
        const bullet = (
          <span
            aria-hidden
            className={cn(
              "font-condensed num relative z-10 flex size-6 items-center justify-center rounded-full text-sm font-semibold",
              done && "bg-primary text-primary-foreground",
              here && "bg-primary text-primary-foreground ring-marker ring-4",
              !done && !here && "border-line-unlit bg-background text-muted-foreground border-2",
            )}
          >
            {i + 1}
          </span>
        );
        const label = (
          <span
            aria-hidden
            className={cn(
              "text-small hidden text-center sm:block",
              here
                ? "text-foreground font-semibold"
                : "text-muted-foreground group-hover:text-foreground",
            )}
          >
            {s}
          </span>
        );
        return (
          <li
            key={s}
            aria-current={here ? "step" : undefined}
            className="relative flex min-w-0 flex-1 flex-col items-center gap-2"
          >
            {/* The line to the next step: ink once that step is reached. */}
            {i < STEPS.length - 1 && (
              <span
                aria-hidden
                className={cn(
                  "absolute top-[10px] left-1/2 h-1 w-full",
                  i < current ? "bg-primary" : "bg-line-unlit",
                )}
              />
            )}
            {done ? (
              <button
                type="button"
                onClick={() => onPick(i)}
                aria-label={`Step ${i + 1}, ${s}, done. Go back to it`}
                className="group focus-visible:ring-ring flex flex-col items-center gap-2 rounded-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
              >
                {bullet}
                {label}
              </button>
            ) : (
              <>
                {bullet}
                {label}
                <span className="sr-only">
                  {here ? `Step ${i + 1}, ${s}, current` : `Step ${i + 1}, ${s}`}
                </span>
              </>
            )}
          </li>
        );
      })}
    </ol>
  );
}
