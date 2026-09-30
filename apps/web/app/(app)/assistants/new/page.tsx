"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type Dispatch, type SetStateAction } from "react";

import { AgentPanel, Field, GuardrailsPanel, MemoryPanel } from "@/components/config/panels";
import { PipelineRecommend } from "@/components/config/PipelineRecommend";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { assist, assistants, meta, type AssistantConfig, type PipelineSuggestion } from "@/lib/api";

type Data = Record<string, unknown>;

const STEPS = ["Basics", "Pipeline", "Agent", "Guardrails", "Memory", "Review"] as const;
type Step = (typeof STEPS)[number];

export default function NewAssistantWizard() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [models, setModels] = useState<string[]>([]);

  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [agentData, setAgentData] = useState<Data>({});
  const [guardrailsData, setGuardrailsData] = useState<Data>({});
  const [memoryData, setMemoryData] = useState<Data>({});
  // The recommended starter pipeline (task 5.6), if the builder took one.
  const [starter, setStarter] = useState<PipelineSuggestion | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void meta.configSchema().then((res) => {
      const def = res.default;
      setAgentData({ system_prompt: def.system_prompt, models: def.models });
      setGuardrailsData(def.guardrails as Data);
      setMemoryData(def.memory as Data);
      setModels(res.allowed_models);
      setReady(true);
    });
  }, []);

  function patch(setter: Dispatch<SetStateAction<Data>>) {
    return (fields: Data) => setter((prev) => ({ ...prev, ...fields }));
  }

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
    } catch {
      setError("Couldn't create the assistant. Try again.");
      setCreating(false);
    }
  }

  if (!ready) {
    return <div className="text-muted-foreground p-10 text-sm">Loading…</div>;
  }

  const main = ((agentData.models as Data)?.main as Data) ?? {};
  const rules = (guardrailsData.rules as string[]) ?? [];

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <div className="flex items-center gap-3">
        <Link href="/assistants" className="text-muted-foreground hover:text-foreground text-sm">
          ←
        </Link>
        <h1 className="font-serif text-2xl font-semibold tracking-tight">Guided setup</h1>
      </div>
      <p className="text-muted-foreground mt-1 text-sm">
        A few questions, then you land on the canvas with a starter pipeline wired up.
      </p>

      <ol className="mt-6 flex gap-2 text-xs">
        {STEPS.map((s, i) => (
          <li
            key={s}
            className={
              "rounded-full px-3 py-1 " +
              (i === step
                ? "bg-primary text-primary-foreground"
                : i < step
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground")
            }
          >
            {i + 1}. {s}
          </li>
        ))}
      </ol>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle className="text-base">{current}</CardTitle>
          {current === "Basics" && (
            <CardDescription>What is this assistant called?</CardDescription>
          )}
          {current === "Pipeline" && (
            <CardDescription>
              Optional: describe it and get a starter pipeline (knowledge base, tools, a system
              prompt and rules) to look over. Or press Next for the basic one.
            </CardDescription>
          )}
          {current === "Agent" && (
            <CardDescription>
              Describe what it should do — this becomes its system prompt — and pick a model.
            </CardDescription>
          )}
          {current === "Guardrails" && (
            <CardDescription>Optional rules and safety defaults.</CardDescription>
          )}
          {current === "Memory" && (
            <CardDescription>How it handles long conversations.</CardDescription>
          )}
          {current === "Review" && (
            <CardDescription>Everything below is editable later on the canvas.</CardDescription>
          )}
        </CardHeader>
        <CardContent>
          {current === "Basics" && (
            <div className="flex flex-col gap-4">
              <Field label="Name">
                <Input
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Support Triage Bot"
                />
              </Field>
              <Field label="Description" hint="Optional — shown in the assistant list.">
                <Input
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Answers billing questions from the docs KB"
                />
              </Field>
            </div>
          )}

          {current === "Pipeline" &&
            (starter ? (
              <div className="flex flex-col gap-3 text-sm">
                <p>
                  Using the recommended pipeline:{" "}
                  {starter.capabilities.map((c) => c.label).join(", ") || "conversation only"}. The
                  next steps show its prompt, rules and memory settings to adjust.
                </p>
                <div>
                  <Button size="sm" variant="outline" onClick={() => setStarter(null)}>
                    Choose again
                  </Button>
                </div>
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

          {current === "Memory" && (
            <MemoryPanel data={memoryData} onChange={patch(setMemoryData)} />
          )}

          {current === "Review" && (
            <div className="flex flex-col gap-3 text-sm">
              <div>
                <div className="text-muted-foreground text-xs">Name</div>
                <div>{name || "(untitled)"}</div>
              </div>
              {description && (
                <div>
                  <div className="text-muted-foreground text-xs">Description</div>
                  <div>{description}</div>
                </div>
              )}
              <div>
                <div className="text-muted-foreground text-xs">Pipeline</div>
                <div>
                  {starter
                    ? starter.capabilities.map((c) => c.label).join(", ") || "Conversation only"
                    : "Basic: no knowledge base or tools yet"}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground text-xs">Model</div>
                <div>
                  {String(main.model ?? "")} · effort {String(main.effort ?? "")}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground text-xs">System prompt</div>
                <div className="text-muted-foreground line-clamp-3">
                  {String(agentData.system_prompt ?? "")}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground text-xs">Guardrail rules</div>
                <div>{rules.length} rule(s)</div>
              </div>
              {error && <p className="text-destructive text-sm">{error}</p>}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="mt-6 flex justify-between">
        <Button
          variant="outline"
          disabled={step === 0}
          onClick={() => setStep((s) => Math.max(0, s - 1))}
        >
          Back
        </Button>
        {current === "Review" ? (
          <Button onClick={create} disabled={creating}>
            {creating ? "Creating…" : "Create assistant"}
          </Button>
        ) : (
          <Button
            disabled={!canAdvance}
            onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}
          >
            Next
          </Button>
        )}
      </div>
    </div>
  );
}
