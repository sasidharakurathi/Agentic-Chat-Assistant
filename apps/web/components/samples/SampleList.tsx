"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { LineBullet, nodeTypeLabel } from "@/components/ui/line-bullet";
import { SectionHeading } from "@/components/ui/section-heading";
import { ApiError, assistants, meta, type SampleSummary } from "@/lib/api";
import { failureMessage } from "@/lib/format";
import { sampleExtras, sampleStops } from "@/lib/samples";

/** The sample assistants shipped with the server (task 6.7): each one a
 *  finished pipeline to open on the canvas, change and chat with. A ruled
 *  list, like the assistants above it; each row draws what joins that
 *  sample's Agent as line bullets, so the rows can be told apart at a
 *  glance. Renders nothing while loading or if the list cannot be read:
 *  samples are a shortcut, not something the page needs. */
export function SampleList() {
  const router = useRouter();
  const [samples, setSamples] = useState<SampleSummary[]>([]);
  const [starting, setStarting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    meta
      .samples()
      .then((found) => {
        if (live) setSamples(found);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  async function start(sample: SampleSummary) {
    setStarting(sample.id);
    setError(null);
    try {
      const made = await assistants.fromSample(sample.id);
      router.push(`/assistants/${made.id}/build`);
    } catch (err) {
      setError(
        failureMessage(
          `Couldn't start from ${sample.name}.`,
          err instanceof ApiError ? err.message : null,
          "Try again.",
        ),
      );
      setStarting(null);
    }
  }

  if (samples.length === 0) return null;

  return (
    <section aria-labelledby="samples-heading" className="mt-10 flex flex-col gap-3">
      <SectionHeading
        id="samples-heading"
        title="Start from a sample"
        description="A finished pipeline to open on the canvas, change, and chat with. Each one becomes a new draft of your own."
      />
      {error && <Alert>{error}</Alert>}
      <ul className="border-border border-t">
        {samples.map((sample) => {
          const stops = sampleStops(sample.node_types);
          const extras = sampleExtras(sample.documents, sample.eval_cases);
          return (
            <li
              key={sample.id}
              className="border-border flex flex-wrap items-start gap-x-6 gap-y-3 border-b py-4"
            >
              <div className="flex min-w-0 flex-[1_1_20rem] flex-col gap-1.5">
                <h3 className="text-reading font-medium">{sample.name}</h3>
                <p className="text-muted-foreground max-w-[60ch] text-sm">
                  {sample.description} {extras}
                </p>
                {stops.length > 0 && (
                  <ul aria-label={`What ${sample.name} uses`} className="mt-1 flex flex-wrap gap-3">
                    {stops.map((stop) => (
                      <li key={stop.type} className="text-small flex items-center gap-1.5">
                        <LineBullet type={stop.type} size={16} decorative />
                        <span>
                          {nodeTypeLabel(stop.type)}
                          {stop.count > 1 && <span className="num"> × {stop.count}</span>}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {sample.needs.map((need) => (
                  <p key={need} className="text-small text-muted-foreground max-w-[60ch]">
                    Needs: {need}
                  </p>
                ))}
              </div>
              <Button
                variant="outline"
                size="sm"
                className="ml-auto"
                disabled={starting !== null}
                aria-label={`Use the ${sample.name} sample`}
                onClick={() => void start(sample)}
              >
                {starting === sample.id ? "Creating…" : "Use sample"}
              </Button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
