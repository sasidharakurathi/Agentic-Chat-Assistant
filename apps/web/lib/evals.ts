import type {
  EvalCaseDraft,
  EvalCaseResult,
  EvalExpected,
  EvalLabels,
  EvalRun,
  EvalRunDetail,
} from "@/lib/api";

/** Pure helpers for the Evals tab (task 6.1): reading a cases file, saying
 *  what a case checks and how it did, and comparing two runs. */

// ── importing cases ──────────────────────────────────────────

/** Columns a CSV may have. Lists go in one cell, separated by `|`. */
export const CSV_COLUMNS = [
  "input",
  "reference",
  "contains",
  "not_contains",
  "tools",
  "cites",
  "refuses",
  "relevant_sources",
] as const;

export type ParsedCases = { cases: EvalCaseDraft[]; problems: string[] };

/** Rows of cells from CSV text: quoted cells may hold commas, line breaks
 *  and doubled quotes. Blank lines are skipped. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  const endRow = () => {
    row.push(cell);
    if (row.some((c) => c.trim() !== "")) rows.push(row);
    row = [];
    cell = "";
  };
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"' && cell === "") quoted = true;
    else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n") endRow();
    else if (ch !== "\r") cell += ch;
  }
  if (cell !== "" || row.length > 0) endRow();
  return rows;
}

const list = (cell: string | undefined) =>
  (cell ?? "")
    .split("|")
    .map((s) => s.trim())
    .filter(Boolean);

const yes = (cell: string | undefined) => /^(true|yes|y|1)$/i.test((cell ?? "").trim());

function fromCsv(text: string): ParsedCases {
  const rows = parseCsv(text.replace(/^﻿/, ""));
  if (rows.length === 0) return { cases: [], problems: ["The file is empty."] };
  const header = rows[0].map((h) => h.trim().toLowerCase());
  const at = (name: string) => header.indexOf(name);
  if (at("input") < 0) {
    return {
      cases: [],
      problems: ['The first row must name the columns, and one must be "input".'],
    };
  }
  const unknown = header.filter((h) => h && !(CSV_COLUMNS as readonly string[]).includes(h));
  const problems = unknown.map((h) => `Column "${h}" isn't used. It was ignored.`);
  const cases: EvalCaseDraft[] = [];
  rows.slice(1).forEach((row, n) => {
    const get = (name: string) => (at(name) >= 0 ? row[at(name)] : undefined);
    const input = (get("input") ?? "").trim();
    if (!input) {
      problems.push(`Row ${n + 2} has no input. It was skipped.`);
      return;
    }
    cases.push({
      input,
      expected: {
        reference: get("reference")?.trim() || null,
        contains: list(get("contains")),
        not_contains: list(get("not_contains")),
        tools: list(get("tools")),
        cites: yes(get("cites")),
        refuses: yes(get("refuses")),
      },
      labels: { relevant_sources: list(get("relevant_sources")) },
    });
  });
  return { cases, problems };
}

const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];

function fromJson(text: string): ParsedCases {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return { cases: [], problems: ["The file isn't valid JSON."] };
  }
  const rows = Array.isArray(data) ? data : (data as { cases?: unknown } | null)?.cases;
  if (!Array.isArray(rows)) {
    return {
      cases: [],
      problems: ['Expected a list of cases, or an object with a "cases" list.'],
    };
  }
  const problems: string[] = [];
  const cases: EvalCaseDraft[] = [];
  rows.forEach((raw, n) => {
    const r = (raw ?? {}) as Record<string, unknown>;
    const input = typeof r.input === "string" ? r.input.trim() : "";
    if (!input) {
      problems.push(`Case ${n + 1} has no input. It was skipped.`);
      return;
    }
    // Expectations may be nested (as the API returns them) or flat (as a
    // CSV row would be).
    const e = (r.expected ?? r) as Record<string, unknown>;
    const l = (r.labels ?? r) as Record<string, unknown>;
    cases.push({
      input,
      expected: {
        reference: typeof e.reference === "string" && e.reference.trim() ? e.reference : null,
        contains: strings(e.contains),
        not_contains: strings(e.not_contains),
        tools: strings(e.tools),
        cites: e.cites === true,
        refuses: e.refuses === true,
      },
      labels: {
        relevant_sources: strings(l.relevant_sources),
        relevant_chunk_ids: strings(l.relevant_chunk_ids),
      },
    });
  });
  return { cases, problems };
}

/** Cases from a `.json` or `.csv` file's text. Rows that can't be used are
 *  skipped and named in `problems`, so one bad row doesn't lose the rest. */
export function parseCasesFile(text: string, filename: string): ParsedCases {
  const json = /\.json$/i.test(filename) || /^\s*[[{]/.test(text);
  return json ? fromJson(text) : fromCsv(text);
}

// ── saying what a case checks ────────────────────────────────

const quote = (items: string[]) => items.map((s) => `"${s}"`).join(", ");

/** What a case asks of its answer, one plain phrase each. */
export function expectations(
  expected: Partial<EvalExpected> | undefined,
  labels: Partial<EvalLabels> | undefined,
): string[] {
  const out: string[] = [];
  if (expected?.refuses) out.push("Should decline");
  if (expected?.contains?.length) out.push(`Says ${quote(expected.contains)}`);
  if (expected?.not_contains?.length) out.push(`Doesn't say ${quote(expected.not_contains)}`);
  if (expected?.tools?.length) out.push(`Uses ${expected.tools.join(", ")}`);
  if (expected?.cites) out.push("Cites a source");
  if (expected?.reference) out.push("Has a reference answer");
  const found = [...(labels?.relevant_sources ?? []), ...(labels?.relevant_chunk_ids ?? [])];
  if (found.length) out.push(`Should find ${found.join(", ")}`);
  return out;
}

export type CheckScore = { kind: string; target: string; passed: boolean };

/** One check, as a sentence about what happened. */
export function checkText(c: CheckScore): string {
  switch (c.kind) {
    case "contains":
      return c.passed ? `Says "${c.target}"` : `Doesn't say "${c.target}"`;
    case "not_contains":
      return c.passed ? `Doesn't say "${c.target}"` : `Says "${c.target}", which it shouldn't`;
    case "tool":
      return c.passed ? `Used ${c.target}` : `Didn't use ${c.target}`;
    case "cites":
      return c.passed ? "Cites a source" : "Cites no source";
    default:
      return `${c.kind} ${c.target}`.trim();
  }
}

export const JUDGE_DIMENSIONS = [
  ["groundedness", "Grounded in its sources"],
  ["correctness", "Correct"],
  ["citation_validity", "Citations hold up"],
  ["refusal_appropriateness", "Declined only when it should"],
] as const;

// ── runs ─────────────────────────────────────────────────────

export function versionLabel(run: Pick<EvalRun, "version_number">): string {
  return run.version_number === null ? "Draft" : `Version ${run.version_number}`;
}

export function isActive(run: Pick<EvalRun, "status">): boolean {
  return run.status === "queued" || run.status === "running";
}

export const RUN_STATUS: Record<
  EvalRun["status"],
  { word: string; tone: "success" | "warning" | "destructive" | "muted" }
> = {
  queued: { word: "Waiting to start", tone: "muted" },
  running: { word: "Running", tone: "warning" },
  done: { word: "Finished", tone: "success" },
  failed: { word: "Stopped early", tone: "destructive" },
  cancelled: { word: "Cancelled", tone: "muted" },
};

/** 0.6667 -> "67%". */
export function percent(ratio: number | null | undefined): string {
  return typeof ratio === "number" ? `${Math.round(ratio * 100)}%` : "Not measured";
}

/** A 1 to 5 mean, or a 0 to 1 retrieval score. */
const score = (n: unknown, digits: number) => (typeof n === "number" ? n.toFixed(digits) : null);

export type Metric = {
  key: string;
  label: string;
  /** For display; null when the run didn't measure it. */
  text: string | null;
  /** For comparing; higher is better. */
  value: number | null;
};

type Metrics = {
  cases?: number;
  passed?: number;
  pass_rate?: number | null;
  judge?: Record<string, number | null> | null;
  retrieval?: Record<string, number | null> | null;
};

/** A run's headline numbers, in the order they are shown. */
export function metricsOf(run: Pick<EvalRun, "metrics">): Metric[] {
  const m = run.metrics as Metrics;
  const out: Metric[] = [
    {
      key: "pass_rate",
      label: "Passed",
      text:
        typeof m.cases === "number" && m.cases > 0
          ? `${m.passed ?? 0} of ${m.cases} (${percent(m.pass_rate)})`
          : null,
      value: m.pass_rate ?? null,
    },
  ];
  for (const [key, label] of JUDGE_DIMENSIONS) {
    const v = m.judge?.[key];
    const text = score(v, 1);
    out.push({
      key,
      label,
      text: text && `${text} of 5`,
      value: typeof v === "number" ? v : null,
    });
  }
  for (const [key, label] of [
    ["recall", "Retrieval recall"],
    ["mrr", "First relevant result (MRR)"],
    ["ndcg", "Retrieval ranking (nDCG)"],
  ] as const) {
    const v = m.retrieval?.[key];
    out.push({ key, label, text: score(v, 2), value: typeof v === "number" ? v : null });
  }
  return out;
}

export type MetricChange = {
  key: string;
  label: string;
  before: string | null;
  after: string | null;
  /** "better" | "worse" | "same", or null when either side didn't measure it. */
  change: "better" | "worse" | "same" | null;
};

export type CaseChange = {
  input: string;
  before: boolean | null;
  after: boolean | null;
  change: "fixed" | "broke" | "same" | "new" | "gone";
};

const keyOf = (r: EvalCaseResult) => r.eval_case_id ?? `input:${r.input}`;

/** What moved between two runs of a suite: each headline number, and each
 *  case that passes now and didn't (fixed), fails now and didn't (broke),
 *  or exists in only one of them. `before` is the baseline. */
export function compareRuns(
  before: EvalRunDetail,
  after: EvalRunDetail,
): { metrics: MetricChange[]; cases: CaseChange[] } {
  const a = metricsOf(before);
  const metrics = metricsOf(after).map((now, i): MetricChange => {
    const was = a[i];
    const both = was.value !== null && now.value !== null;
    const delta = both ? (now.value as number) - (was.value as number) : 0;
    return {
      key: now.key,
      label: now.label,
      before: was.text,
      after: now.text,
      change: !both ? null : Math.abs(delta) < 1e-9 ? "same" : delta > 0 ? "better" : "worse",
    };
  });

  const earlier = new Map(before.results.map((r) => [keyOf(r), r]));
  const seen = new Set<string>();
  const cases: CaseChange[] = after.results.map((r) => {
    const was = earlier.get(keyOf(r));
    seen.add(keyOf(r));
    const change = !was ? "new" : was.passed === r.passed ? "same" : r.passed ? "fixed" : "broke";
    return { input: r.input, before: was ? was.passed : null, after: r.passed, change };
  });
  for (const r of before.results) {
    if (!seen.has(keyOf(r))) {
      cases.push({ input: r.input, before: r.passed, after: null, change: "gone" });
    }
  }
  return { metrics, cases };
}
