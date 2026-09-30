/** Parsing the MCP server form's text boxes. Messages say what is wrong and
 *  what to do, in sentence case, for showing as they are. */

export type Parsed<T> = { value: T; error: null } | { value: null; error: string };

/** One argument per line. Blank lines are dropped; spaces inside a line are
 *  kept, because each line is one argv entry, never split by a shell. */
export function parseArgs(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
}

/** `KEY=value` (env) or `Name: value` (headers), one per line. */
export function parsePairs(text: string, kind: "env" | "headers"): Parsed<Record<string, string>> {
  const sep = kind === "env" ? "=" : ":";
  const out: Record<string, string> = {};
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const at = line.indexOf(sep);
    if (at <= 0) {
      const example = kind === "env" ? "API_KEY=abc123" : "Authorization: Bearer abc123";
      return { value: null, error: `Line ${i + 1} should look like ${example}.` };
    }
    const key = line.slice(0, at).trim();
    const value = line.slice(at + 1).trim();
    if (key in out) return { value: null, error: `${key} appears twice. Keep one line for it.` };
    out[key] = value;
  }
  return { value: out, error: null };
}

/** The same rule as the server: lowercase letters, digits, hyphens, starting
 *  with a letter, up to 40. It becomes part of every tool name. */
export function nameProblem(name: string): string | null {
  if (!name) return "Give the server a name.";
  if (!/^[a-z][a-z0-9-]{0,39}$/.test(name))
    return "Use lowercase letters, digits and hyphens, starting with a letter.";
  if (name === "caps") return "That name is reserved. Pick another.";
  return null;
}

/** Secrets a preset needs that the form still has empty or missing. */
export function missingSecrets(required: string[], filled: Record<string, string>): string[] {
  return required.filter((name) => !(filled[name] ?? "").trim());
}

export type Limits = {
  memory_mb: number;
  cpu_seconds: number;
  max_processes: number;
  max_open_files: number;
  max_file_mb: number;
  wall_clock_s: number;
  idle_timeout_s: number;
};

function duration(s: number): string {
  if (s % 3600 === 0) return `${s / 3600} h`;
  if (s % 60 === 0) return `${s / 60} min`;
  return `${s} s`;
}

/** One line a person can read: "1 GB memory, 10 min of CPU time, …". */
export function describeLimits(l: Limits): string {
  const memory = l.memory_mb % 1024 === 0 ? `${l.memory_mb / 1024} GB` : `${l.memory_mb} MB`;
  return [
    `${memory} memory`,
    `${duration(l.cpu_seconds)} of CPU time`,
    `${l.max_processes} processes`,
    `${duration(l.wall_clock_s)} per session`,
    `stops after ${duration(l.idle_timeout_s)} idle`,
  ].join(", ");
}
