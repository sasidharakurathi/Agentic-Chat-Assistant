import { describe, expect, it } from "vitest";

import { describeLimits, missingSecrets, nameProblem, parseArgs, parsePairs } from "./mcp-forms";

describe("parseArgs", () => {
  it("keeps one argument per line, spaces and all", () => {
    expect(parseArgs("-y\n @scope/server \n\n--root\nC:\\My Files")).toEqual([
      "-y",
      "@scope/server",
      "--root",
      "C:\\My Files",
    ]);
  });
});

describe("parsePairs", () => {
  it("reads env lines, keeping = inside values", () => {
    expect(parsePairs("API_KEY=abc=def\n\nMODE = fast", "env")).toEqual({
      value: { API_KEY: "abc=def", MODE: "fast" },
      error: null,
    });
  });

  it("reads header lines, keeping : inside values", () => {
    expect(parsePairs("Authorization: Bearer x:y", "headers").value).toEqual({
      Authorization: "Bearer x:y",
    });
  });

  it("says which line is wrong, and refuses duplicates", () => {
    expect(parsePairs("A=1\nnot a pair", "env").error).toBe(
      "Line 2 should look like API_KEY=abc123",
    );
    expect(parsePairs("A=1\nA=2", "env").error).toBe("A appears twice");
  });
});

describe("nameProblem", () => {
  it("matches the server's rule", () => {
    expect(nameProblem("github-tools")).toBeNull();
    expect(nameProblem("GitHub")).not.toBeNull();
    expect(nameProblem("my_tools")).not.toBeNull();
    expect(nameProblem("caps")).toBe("That name is reserved.");
  });
});

describe("describeLimits", () => {
  it("reads like a sentence, in sensible units", () => {
    expect(
      describeLimits({
        memory_mb: 1024,
        cpu_seconds: 600,
        max_processes: 128,
        max_open_files: 256,
        max_file_mb: 64,
        wall_clock_s: 3600,
        idle_timeout_s: 90,
      }),
    ).toBe("1 GB memory · 10 min CPU · 128 processes · 1 h per session · stops after 90 s idle");
  });
});

describe("missingSecrets", () => {
  it("lists required names that are absent or blank", () => {
    expect(missingSecrets(["A", "B", "C"], { A: "x", B: "  " })).toEqual(["B", "C"]);
  });
  it("is empty when every required name has a value", () => {
    expect(missingSecrets(["Authorization"], { Authorization: "Bearer t" })).toEqual([]);
    expect(missingSecrets([], {})).toEqual([]);
  });
});
