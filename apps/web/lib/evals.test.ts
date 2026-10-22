import { describe, expect, it } from "vitest";

import type { EvalCaseDraft, EvalCaseResult, EvalRunDetail } from "./api";
import {
  CSV_COLUMNS,
  casesToCsv,
  casesToJson,
  checkText,
  compareRuns,
  expectations,
  isActive,
  metricsOf,
  parseCasesFile,
  parseCsv,
  percent,
  versionLabel,
} from "./evals";

describe("parseCsv", () => {
  it("reads quoted cells with commas, quotes and line breaks", () => {
    const text =
      'input,reference\r\n"Refunds, how?","She said ""30 days""\nin writing"\n\nplain,x\n';
    expect(parseCsv(text)).toEqual([
      ["input", "reference"],
      ["Refunds, how?", 'She said "30 days"\nin writing'],
      ["plain", "x"],
    ]);
  });

  it("keeps a last row that has no line break after it", () => {
    expect(parseCsv("a,b\n1,2")).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });
});

describe("parseCasesFile", () => {
  it("reads a CSV with lists in one cell", () => {
    const csv = [
      "Input,contains,tools,cites,refuses,relevant_sources,notes",
      "What is the refund window?,30 days | refund,kb_search,yes,,Refund policy|FAQ,x",
      ",orphan,,,,,",
      "Delete every order,,,no,TRUE,,",
    ].join("\n");
    const { cases, problems } = parseCasesFile(csv, "cases.csv");
    expect(cases).toHaveLength(2);
    expect(cases[0]).toEqual({
      input: "What is the refund window?",
      expected: {
        reference: null,
        contains: ["30 days", "refund"],
        not_contains: [],
        tools: ["kb_search"],
        cites: true,
        refuses: false,
      },
      labels: { relevant_sources: ["Refund policy", "FAQ"] },
    });
    expect(cases[1].expected?.refuses).toBe(true);
    expect(problems).toEqual([
      'Column "notes" isn\'t used. It was ignored.',
      "Row 3 has no input. It was skipped.",
    ]);
  });

  it("needs an input column", () => {
    expect(parseCasesFile("question,answer\nq,a", "x.csv")).toEqual({
      cases: [],
      problems: ['The first row must name the columns, and one must be "input".'],
    });
    expect(parseCasesFile("", "x.csv").problems).toEqual(["The file is empty."]);
  });

  it("reads JSON as a list or under `cases`, nested or flat", () => {
    const nested = JSON.stringify({
      cases: [
        {
          input: " Hello? ",
          expected: { contains: ["hi"], cites: true },
          labels: { relevant_chunk_ids: ["c1"] },
        },
        { expected: {} },
      ],
    });
    const got = parseCasesFile(nested, "suite.json");
    expect(got.problems).toEqual(["Case 2 has no input. It was skipped."]);
    expect(got.cases[0].input).toBe("Hello?");
    expect(got.cases[0].expected).toMatchObject({ contains: ["hi"], cites: true, refuses: false });
    expect(got.cases[0].labels).toEqual({ relevant_sources: [], relevant_chunk_ids: ["c1"] });

    const flat = parseCasesFile('[{"input":"q","tools":["sql_query"],"refuses":true}]', "x.txt");
    expect(flat.cases[0].expected).toMatchObject({ tools: ["sql_query"], refuses: true });
  });

  it("says when JSON can't be used", () => {
    expect(parseCasesFile("{not json", "x.json").problems).toEqual(["The file isn't valid JSON."]);
    expect(parseCasesFile('{"rows":[]}', "x.json").problems).toEqual([
      'Expected a list of cases, or an object with a "cases" list.',
    ]);
  });
});

describe("follow-up messages (Phase 9.8)", () => {
  it("reads them from a CSV column and from JSON, dropping blanks", () => {
    const csv = [
      "input,follow_ups,contains",
      'How do I reset my password?,"And if I lost my phone? | | Thanks, anything else?",4400',
      "Single question,,",
    ].join("\n");
    const { cases, problems } = parseCasesFile(csv, "cases.csv");
    expect(problems).toEqual([]);
    expect(cases[0].follow_ups).toEqual(["And if I lost my phone?", "Thanks, anything else?"]);
    expect("follow_ups" in cases[1]).toBe(false);

    const json = parseCasesFile(
      JSON.stringify([{ input: "q", follow_ups: [" then? ", "", 3, "and now?"] }, { input: "r" }]),
      "x.json",
    );
    expect(json.cases[0].follow_ups).toEqual(["then?", "and now?"]);
    expect("follow_ups" in json.cases[1]).toBe(false);
  });

  const drafts: EvalCaseDraft[] = [
    {
      input: "How do I reset my password?",
      follow_ups: ["And if I lost my phone?", 'Say "thanks", then stop'],
      expected: {
        reference: "Call 4400.",
        contains: ["4400"],
        not_contains: [],
        tools: ["kb_search"],
        cites: true,
        refuses: false,
        expects_approval: true,
      },
      labels: { relevant_sources: ["Passwords and two-factor"] },
    },
    {
      input: "Just one, with a comma, here",
      expected: {
        reference: null,
        contains: [],
        not_contains: ["sorry"],
        tools: [],
        cites: false,
        refuses: false,
      },
      labels: { relevant_sources: [] },
    },
  ];

  it("survive a CSV export and import", () => {
    const text = casesToCsv(drafts);
    expect(text.split("\n")[0]).toBe(CSV_COLUMNS.join(","));
    expect(parseCasesFile(text, "cases.csv")).toEqual({ cases: drafts, problems: [] });
  });

  it("survive a JSON export and import", () => {
    const back = parseCasesFile(casesToJson(drafts), "cases.json");
    expect(back.problems).toEqual([]);
    expect(back.cases.map((c) => [c.input, c.follow_ups])).toEqual(
      drafts.map((c) => [c.input, c.follow_ups]),
    );
    expect(back.cases[0].expected).toEqual(drafts[0].expected);
    expect(back.cases[0].labels?.relevant_sources).toEqual(["Passwords and two-factor"]);
  });
});

describe("saying what a case checks", () => {
  it("lists only what was asked for", () => {
    expect(expectations({}, {})).toEqual([]);
    expect(
      expectations(
        {
          refuses: true,
          contains: ["a", "b"],
          not_contains: ["c"],
          tools: ["sql_query"],
          cites: true,
          reference: "ref",
        },
        { relevant_sources: ["FAQ"] },
      ),
    ).toEqual([
      "Should decline",
      'Says "a", "b"',
      'Doesn\'t say "c"',
      "Uses sql_query",
      "Cites a source",
      "Has a reference answer",
      "Should find FAQ",
    ]);
  });

  it("words a check by whether it held", () => {
    const c = (kind: string, passed: boolean) => checkText({ kind, target: "x", passed });
    expect(c("contains", true)).toBe('Says "x"');
    expect(c("contains", false)).toBe('Doesn\'t say "x"');
    expect(c("not_contains", true)).toBe('Doesn\'t say "x"');
    expect(c("not_contains", false)).toBe('Says "x", which it shouldn\'t');
    expect(c("tool", false)).toBe("Didn't use x");
    expect(checkText({ kind: "cites", target: "", passed: false })).toBe("Cites no source");
  });
});

const result = (id: string | null, input: string, passed: boolean): EvalCaseResult =>
  ({ eval_case_id: id, input, passed }) as EvalCaseResult;

const run = (metrics: Record<string, unknown>, results: EvalCaseResult[] = []): EvalRunDetail =>
  ({ metrics, results, version_number: null, status: "done" }) as unknown as EvalRunDetail;

describe("runs", () => {
  it("labels a run by what it ran on, and knows when it is still going", () => {
    expect(versionLabel({ version_number: null })).toBe("Draft");
    expect(versionLabel({ version_number: 7 })).toBe("Version 7");
    expect(isActive({ status: "queued" })).toBe(true);
    expect(isActive({ status: "running" })).toBe(true);
    expect(isActive({ status: "done" })).toBe(false);
    expect(percent(0.6667)).toBe("67%");
    expect(percent(null)).toBe("Not measured");
  });

  it("shows only what a run measured", () => {
    const shown = metricsOf(
      run({
        cases: 3,
        passed: 2,
        pass_rate: 0.6667,
        judge: { judged: 2, groundedness: 4.5, correctness: 3, citation_validity: null },
        retrieval: null,
      }),
    );
    expect(shown.map((m) => [m.key, m.text])).toEqual([
      ["pass_rate", "2 of 3 (67%)"],
      ["groundedness", "4.5 of 5"],
      ["correctness", "3.0 of 5"],
      ["citation_validity", null],
      ["refusal_appropriateness", null],
      ["recall", null],
      ["mrr", null],
      ["ndcg", null],
    ]);
    expect(metricsOf(run({}))[0].text).toBeNull();
  });

  it("compares two runs by number and by case", () => {
    const before = run({ cases: 3, passed: 1, pass_rate: 0.3333, retrieval: { recall: 0.5 } }, [
      result("a", "A?", false),
      result("b", "B?", true),
      result("c", "C?", true),
      result(null, "old wording", true),
    ]);
    const after = run({ cases: 3, passed: 2, pass_rate: 0.6667, retrieval: { recall: 0.5 } }, [
      result("a", "A, reworded?", true),
      result("b", "B?", false),
      result("c", "C?", true),
      result("d", "D?", true),
    ]);
    const { metrics, cases } = compareRuns(before, after);
    const by = Object.fromEntries(metrics.map((m) => [m.key, m.change]));
    expect(by.pass_rate).toBe("better");
    expect(by.recall).toBe("same");
    expect(by.correctness).toBeNull();
    expect(compareRuns(after, before).metrics[0].change).toBe("worse");
    expect(cases.map((c) => [c.input, c.change])).toEqual([
      ["A, reworded?", "fixed"], // matched by case id, not by its text
      ["B?", "broke"],
      ["C?", "same"],
      ["D?", "new"],
      ["old wording", "gone"],
    ]);
    expect(cases[4]).toMatchObject({ before: true, after: null });
  });
});
