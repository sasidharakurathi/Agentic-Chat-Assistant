/** Chat slash commands: typed in the message box, handled by the page, never
 *  sent to the model. */

export type SlashCommandName =
  "help" | "new" | "clear" | "rename" | "archive" | "retry" | "stop" | "cost" | "export" | "memory";

export type SlashCommand = {
  name: SlashCommandName;
  /** Shown after the name in the menu, e.g. "<title>". */
  args?: string;
  description: string;
  /** The command needs its argument; Enter completes it rather than running it. */
  needsArg?: boolean;
};

export const SLASH_COMMANDS: SlashCommand[] = [
  { name: "help", description: "Show these commands" },
  { name: "new", description: "Start a new conversation" },
  { name: "clear", description: "Start fresh: a new conversation with no history" },
  {
    name: "rename",
    args: "<title>",
    description: "Rename this conversation",
    needsArg: true,
  },
  { name: "archive", description: "Archive this conversation" },
  { name: "retry", description: "Send your last message again" },
  { name: "stop", description: "Stop the turn that is running" },
  { name: "cost", description: "Show what this conversation has cost" },
  { name: "export", description: "Download this conversation as Markdown" },
  { name: "memory", description: "See what this assistant remembers about you, or forget it" },
];

export type ParsedInput =
  | { kind: "message"; text: string }
  | { kind: "command"; name: SlashCommandName; arg: string }
  | { kind: "unknown"; name: string };

/** What the message box holds: a message, a known command, or an unknown one.
 *  `//` at the start sends a message that begins with a slash. */
export function parseInput(raw: string): ParsedInput {
  const text = raw.trim();
  if (text.startsWith("//")) return { kind: "message", text: text.slice(1) };
  if (!text.startsWith("/")) return { kind: "message", text };
  const [head, ...rest] = text.slice(1).split(/\s+/);
  const name = head.toLowerCase();
  const known = SLASH_COMMANDS.find((c) => c.name === name);
  if (!known) return { kind: "unknown", name };
  return { kind: "command", name: known.name, arg: rest.join(" ").trim() };
}

/** Commands to offer while the user is typing one: the box holds `/` plus a
 *  prefix and no space yet. Empty when the box isn't a command in progress. */
export function suggestCommands(raw: string): SlashCommand[] {
  if (!raw.startsWith("/") || raw.startsWith("//") || /\s/.test(raw)) return [];
  const prefix = raw.slice(1).toLowerCase();
  return SLASH_COMMANDS.filter((c) => c.name.startsWith(prefix));
}

export type ExportMessage = { role: string; content: string; created_at?: string };

/** A conversation as a Markdown document, for /export. */
export function conversationMarkdown(title: string, messages: ExportMessage[]): string {
  const parts = [`# ${title}`, ""];
  for (const m of messages) {
    const who = m.role === "user" ? "You" : m.role === "assistant" ? "Assistant" : m.role;
    const when = m.created_at ? ` (${new Date(m.created_at).toLocaleString()})` : "";
    parts.push(`## ${who}${when}`, "", m.content.trim(), "");
  }
  return parts.join("\n");
}
