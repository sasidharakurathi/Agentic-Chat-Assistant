"use client";

import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { ApprovalCard, type PendingApproval } from "@/components/chat/ApprovalCard";
import { RunDetails } from "@/components/chat/RunDetails";
import { Markdown } from "@/components/chat/Markdown";
import { SourcesPanel } from "@/components/chat/SourcesPanel";
import { RunningBadge, TypingDots } from "@/components/chat/TypingDots";
import { ToolCallCard, type ToolCallView } from "@/components/chat/ToolCallCard";
import { GuardrailNotes } from "@/components/chat/GuardrailNotes";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useConfirm } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { splitBlocks } from "@/lib/message-blocks";
import {
  approvals as approvalsApi,
  conversations,
  streamMessage,
  type ChatMessage,
  type Citation,
  type MessageBlock,
  memories as memoriesApi,
  type MemoryFile,
  type GuardrailFinding,
} from "@/lib/api";
import { titleWithPending } from "@/lib/approvals";
import { formatCount, formatUsd } from "@/lib/format";
import {
  SLASH_COMMANDS,
  conversationMarkdown,
  parseInput,
  suggestCommands,
  type SlashCommand,
  type SlashCommandName,
} from "@/lib/slash-commands";
import { appendSubagentText, nestCalls } from "@/lib/tool-tree";
import { cn } from "@/lib/utils";

type Live = {
  text: string;
  tools: ToolCallView[];
  citations: Citation[];
  guardrails: GuardrailFinding[];
};
/** What the conversation has cost so far (task 1.8). */
type Spend = { cost: number; tokensIn: number; tokensOut: number };

export function ChatThread({
  conversationId,
  assistantId,
  title = "Conversation",
  onNewChat,
  onRename,
  onArchive,
  onTitle,
}: {
  conversationId: string;
  assistantId: string;
  title?: string;
  /** The first turn named the conversation (task 5.2). */
  onTitle?: (title: string) => void;
  /** For the slash commands that act on the conversation list. */
  onNewChat?: () => void;
  onRename?: (title: string) => void;
  onArchive?: () => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [live, setLive] = useState<Live | null>(null);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** The typed failure behind `error`, when the turn reported one (5.4). */
  const [failure, setFailure] = useState<{ code: string; retryable: boolean } | null>(null);
  /** Budget warnings from the last turn (task 5.7). */
  const [budgetNotes, setBudgetNotes] = useState<string[]>([]);
  const [input, setInput] = useState("");
  // Approvals are kept outside `live` on purpose: a pending one must survive
  // the stream ending (a turn can finish denied-by-timeout) and must also be
  // restorable after a reload, which `live` never is.
  const [pending, setPending] = useState<PendingApproval[]>([]);
  const toast = useToast();
  const [stopping, setStopping] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  // The in-flight stream, so leaving the conversation mid-turn drops it
  // rather than leaving it running in the background.
  const abortRef = useRef<AbortController | null>(null);

  // Cursor for history older than what is shown; null when it is all here.
  // The conversation detail only carries the latest page of messages.
  const [older, setOlder] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [spend, setSpend] = useState<Spend | null>(null);

  const loadMessages = useCallback(async () => {
    const c = await conversations.get(conversationId);
    setMessages(c.messages);
    setOlder(c.messages_next_cursor);
    // The server's figure: every turn's model spend plus retrieval (embedding
    // and rerank), which the live usage events below never include.
    setSpend({
      cost: Number(c.cost_usd),
      tokensIn: Number(c.token_usage?.in ?? 0),
      tokensOut: Number(c.token_usage?.out ?? 0),
    });
  }, [conversationId]);

  const loadOlder = useCallback(async () => {
    if (!older) return;
    setLoadingOlder(true);
    try {
      const page = await conversations.olderMessages(conversationId, older);
      const el = scrollRef.current;
      if (el) keepFromBottom.current = el.scrollHeight - el.scrollTop;
      setMessages((m) => [...page.items, ...m]);
      setOlder(page.next_cursor);
    } finally {
      setLoadingOlder(false);
    }
  }, [conversationId, older]);

  // The approval_required SSE event only reaches whoever was watching the
  // stream. Re-fetching on mount is what makes a reload (or a second tab)
  // still able to answer a decision that is genuinely still waiting.
  const loadPending = useCallback(async () => {
    try {
      const rows = await approvalsApi.listPending(conversationId);
      setPending(
        rows.map((r) => ({
          approval_id: r.id,
          tool: r.tool_name,
          input: r.input,
          risk: r.risk,
          rationale: r.rationale ?? "",
          expires_at: r.expires_at,
        })),
      );
    } catch {
      /* a failed poll must not break the thread */
    }
  }, [conversationId]);

  useEffect(() => {
    setLive(null);
    setPendingUser(null);
    setError(null);
    setFailure(null);
    setPending([]);
    setSpend(null);
    void loadMessages();
    void loadPending();
    // Switching conversation or unmounting: the server records a dropped
    // stream as an aborted turn, so nothing is lost by letting go of it.
    return () => abortRef.current?.abort();
  }, [loadMessages, loadPending]);

  // While approvals wait, the tab says so: the assistant is stuck on a
  // person, who may be looking at another tab (task 5.9).
  useEffect(() => {
    document.title = titleWithPending(document.title, pending.length);
  }, [pending.length]);
  useEffect(() => () => void (document.title = titleWithPending(document.title, 0)), []);

  const stop = useCallback(async () => {
    setStopping(true);
    try {
      await conversations.interrupt(conversationId);
    } catch {
      // If the stop request itself fails, dropping the connection is the
      // fallback — the turn is still stopped and recorded, just less gently.
      abortRef.current?.abort();
    }
  }, [conversationId]);

  // Distance from the bottom to restore after older history is prepended;
  // otherwise every change pins the view to the newest message.
  const keepFromBottom = useRef<number | null>(null);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (keepFromBottom.current !== null) {
      el.scrollTop = el.scrollHeight - keepFromBottom.current;
      keepFromBottom.current = null;
      return;
    }
    el.scrollTo({ top: el.scrollHeight });
  }, [messages, live, pending]);

  const sendText = useCallback(
    async (text: string) => {
      if (!text || sending) return;
      setError(null);
      setFailure(null);
      setBudgetNotes([]);
      setPendingUser(text);
      setLive({ text: "", tools: [], citations: [], guardrails: [] });
      setSending(true);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await streamMessage(
          conversationId,
          text,
          (e) => {
            setLive((prev) => {
              if (!prev) return prev;
              switch (e.type) {
                case "token":
                  // A subagent's notes go on its delegation card, not into the
                  // answer (task 2.10).
                  return e.parent_id
                    ? { ...prev, tools: appendSubagentText(prev.tools, e.parent_id, e.text) }
                    : { ...prev, text: prev.text + e.text };
                case "tool_call":
                  return {
                    ...prev,
                    tools: [
                      ...prev.tools,
                      { id: e.id, name: e.name, input: e.input, parent_id: e.parent_id },
                    ],
                  };
                case "tool_result":
                  return {
                    ...prev,
                    tools: prev.tools.map((t) =>
                      t.id === e.id
                        ? { ...t, status: e.status, output: e.output, permission: e.permission }
                        : t,
                    ),
                  };
                case "approval_required":
                  setPending((p) => [
                    ...p.filter((x) => x.approval_id !== e.approval_id),
                    {
                      approval_id: e.approval_id,
                      tool: e.tool,
                      input: e.input,
                      risk: e.risk,
                      rationale: e.rationale,
                      expires_at: e.expires_at,
                    },
                  ]);
                  return prev;
                case "guardrail":
                  return { ...prev, guardrails: [...prev.guardrails, e] };
                case "citation":
                  // Arrives at finalize, after the full answer — a [n] marker only
                  // means something once every search in the turn has been seen.
                  return { ...prev, citations: [...prev.citations, e as Citation] };
                default:
                  return prev;
              }
            });
            if (e.type === "error") {
              setError(e.message);
              setFailure({ code: e.code, retryable: Boolean(e.retryable) });
            }
            if (e.type === "title") onTitle?.(e.title);
            if (e.type === "budget") setBudgetNotes((n) => [...n, e.message]);
            // Spend as it is incurred, so a long turn is visibly costing money;
            // replaced by the server's total once the turn is saved.
            if (e.type === "usage") {
              setSpend((s) =>
                s
                  ? {
                      cost: s.cost + e.cost_usd,
                      tokensIn: s.tokensIn + e.tokens_in,
                      tokensOut: s.tokensOut + e.tokens_out,
                    }
                  : s,
              );
            }
          },
          controller.signal,
        );
        await loadMessages();
      } catch (err) {
        // Our own abort (leaving the conversation) is not an error to show.
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "stream failed");
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setLive(null);
        setPendingUser(null);
        setSending(false);
        setStopping(false);
        // A turn that ends (finished, stopped, failed) settles any approval it
        // raised: stopped ones are expired by the server. Re-read what is really
        // still pending, or a stale card sits there answering "already expired"
        // next to the new turn's card.
        if (!controller.signal.aborted) void loadPending();
      }
    },
    [conversationId, sending, loadMessages, loadPending, onTitle],
  );

  // ── slash commands ──────────────────────────────────────────
  const [menuIndex, setMenuIndex] = useState(0);
  const [menuDismissed, setMenuDismissed] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const suggestions = menuDismissed ? [] : suggestCommands(input);

  const confirm = useConfirm();
  /** `/memory`: the memory tool's notes about you, with a way to forget them. */
  const showMemory = useCallback(async () => {
    let files: MemoryFile[];
    try {
      files = await memoriesApi.list(assistantId);
    } catch {
      toast("Couldn't load what this assistant remembers.");
      return;
    }
    if (files.length === 0) {
      toast("This assistant remembers nothing about you.");
      return;
    }
    const forget = await confirm({
      title: "What this assistant remembers about you",
      description: (
        <div className="flex max-h-80 flex-col gap-3 overflow-auto">
          {files.map((f) => (
            <div key={f.path}>
              <p className="font-mono text-xs font-medium">{f.path}</p>
              <pre className="bg-muted mt-1 rounded p-2 text-xs whitespace-pre-wrap">
                {f.content}
              </pre>
            </div>
          ))}
        </div>
      ),
      confirmLabel: "Forget all of it",
      destructive: true,
    });
    if (!forget) return;
    const { deleted } = await memoriesApi.clear(assistantId);
    toast(`Forgotten (${deleted} ${deleted === 1 ? "file" : "files"}).`);
  }, [assistantId, confirm, toast]);

  const runCommand = useCallback(
    (name: SlashCommandName, arg: string) => {
      const busyOnly = new Set<SlashCommandName>(["help", "stop", "cost", "export"]);
      if (sending && !busyOnly.has(name)) {
        toast(`Wait for this turn to finish, or /stop it first.`);
        return;
      }
      switch (name) {
        case "help":
          setShowHelp(true);
          return;
        case "new":
        case "clear":
          onNewChat?.();
          return;
        case "rename":
          if (!arg) {
            toast("Add a title: /rename <title>");
            return;
          }
          onRename?.(arg);
          return;
        case "archive":
          onArchive?.();
          return;
        case "retry": {
          const last = [...messages].reverse().find((m) => m.role === "user");
          if (!last) toast("Nothing to retry yet.");
          else void sendText(last.content);
          return;
        }
        case "stop":
          if (sending) void stop();
          else toast("Nothing is running.");
          return;
        case "cost":
          toast(
            spend
              ? `This conversation has cost ${formatUsd(spend.cost)} · ${formatCount(spend.tokensIn + spend.tokensOut)} tokens`
              : "No spend recorded yet.",
          );
          return;
        case "memory":
          void showMemory();
          return;
        case "export": {
          const blob = new Blob([conversationMarkdown(title, messages)], {
            type: "text/markdown",
          });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `${title.replace(/[^\w.-]+/g, "-").slice(0, 60) || "conversation"}.md`;
          a.click();
          URL.revokeObjectURL(url);
          return;
        }
      }
    },
    [
      sending,
      toast,
      onNewChat,
      onRename,
      onArchive,
      messages,
      sendText,
      stop,
      spend,
      title,
      showMemory,
    ],
  );

  const submit = useCallback(() => {
    const parsed = parseInput(input);
    if (parsed.kind === "message") {
      if (!parsed.text) return;
      setInput("");
      void sendText(parsed.text);
      return;
    }
    if (parsed.kind === "unknown") {
      toast(`Unknown command /${parsed.name}. Type /help to see them all.`);
      return;
    }
    setInput("");
    runCommand(parsed.name, parsed.arg);
  }, [input, sendText, runCommand, toast]);

  const pick = (cmd: SlashCommand) => {
    if (cmd.needsArg) {
      setInput(`/${cmd.name} `);
      return;
    }
    setInput("");
    runCommand(cmd.name, "");
  };

  return (
    <div className="flex h-full flex-col">
      {spend && (
        <div
          className="border-border text-muted-foreground flex justify-end border-b px-6 py-1.5 text-xs"
          aria-label="Conversation spend"
          title={`${spend.tokensIn.toLocaleString()} tokens in, ${spend.tokensOut.toLocaleString()} out`}
        >
          <span className="text-foreground font-medium tabular-nums">{formatUsd(spend.cost)}</span>
          <span className="mx-1.5">·</span>
          <span className="tabular-nums">
            {formatCount(spend.tokensIn + spend.tokensOut)} tokens
          </span>
          {sending && <RunningBadge />}
        </div>
      )}
      <div ref={scrollRef} className="flex-1 space-y-4 overflow-auto p-6">
        {older && (
          <div className="flex justify-center">
            <button
              className="text-muted-foreground hover:text-foreground text-xs"
              disabled={loadingOlder}
              onClick={() => void loadOlder()}
            >
              {loadingOlder ? "Loading…" : "Load earlier messages"}
            </button>
          </div>
        )}
        {messages.map((m) => (
          <Bubble
            key={m.id}
            role={m.role}
            text={m.content}
            blocks={m.blocks}
            assistantId={assistantId}
            run={m.role === "assistant" ? { conversationId, messageId: m.id } : undefined}
          />
        ))}
        {pendingUser && <Bubble role="user" text={pendingUser} assistantId={assistantId} />}
        {live && (
          <div className="space-y-2">
            <ToolCalls calls={live.tools} />
            <Bubble
              role="assistant"
              text={live.text}
              working
              citations={live.citations}
              guardrails={live.guardrails}
              assistantId={assistantId}
            />
          </div>
        )}
        {pending.map((a) => (
          <ApprovalCard
            key={a.approval_id}
            approval={a}
            onDecided={() => setPending((p) => p.filter((x) => x.approval_id !== a.approval_id))}
            onGone={(reason) => {
              setPending((p) => p.filter((x) => x.approval_id !== a.approval_id));
              toast(`That request is closed: ${reason}`);
            }}
          />
        ))}
        {budgetNotes.map((note) => (
          // A budget 80% or more used (task 5.7): said, not blocking.
          <p
            key={note}
            role="status"
            className="border-warning bg-warning/10 rounded-md border px-3 py-2 text-xs"
          >
            {note}
          </p>
        ))}
        {error && (
          <TurnError
            message={error}
            failure={failure}
            onRetry={
              failure?.retryable && !sending
                ? () => {
                    const last = [...messages].reverse().find((m) => m.role === "user");
                    if (last) void sendText(last.content);
                  }
                : undefined
            }
          />
        )}
      </div>
      <div className="border-border relative border-t p-4">
        {suggestions.length > 0 && (
          <ul
            role="listbox"
            aria-label="Commands"
            className="bg-background border-border absolute right-4 bottom-full left-4 mb-2 overflow-hidden rounded-md border shadow-lg"
          >
            {suggestions.map((c, i) => (
              <li
                key={c.name}
                role="option"
                aria-selected={i === Math.min(menuIndex, suggestions.length - 1)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(c);
                }}
                className={cn(
                  "flex cursor-pointer items-baseline gap-2 px-3 py-1.5 text-sm",
                  i === Math.min(menuIndex, suggestions.length - 1) && "bg-muted",
                )}
              >
                <span className="font-mono">/{c.name}</span>
                {c.args && (
                  <span className="text-muted-foreground font-mono text-xs">{c.args}</span>
                )}
                <span className="text-muted-foreground ml-auto text-xs">{c.description}</span>
              </li>
            ))}
          </ul>
        )}
        {showHelp && (
          <div className="bg-background border-border absolute right-4 bottom-full left-4 mb-2 rounded-md border p-3 text-sm shadow-lg">
            <div className="mb-2 flex items-center justify-between">
              <span className="font-medium">Commands</span>
              <button
                className="text-muted-foreground hover:text-foreground text-xs"
                onClick={() => setShowHelp(false)}
              >
                Close
              </button>
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
              {SLASH_COMMANDS.map((c) => (
                <Fragment key={c.name}>
                  <dt className="font-mono">
                    /{c.name}
                    {c.args ? ` ${c.args}` : ""}
                  </dt>
                  <dd className="text-muted-foreground">{c.description}</dd>
                </Fragment>
              ))}
            </dl>
            <p className="text-muted-foreground mt-2 text-xs">
              Start a message with // to send text that begins with a slash.
            </p>
          </div>
        )}
        <div className="flex gap-2">
          <Textarea
            rows={2}
            placeholder="Message the assistant…  (Enter to send, / for commands)"
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              setMenuIndex(0);
              setMenuDismissed(false);
              setShowHelp(false);
            }}
            onKeyDown={(e) => {
              if (suggestions.length > 0) {
                const current = suggestions[Math.min(menuIndex, suggestions.length - 1)];
                if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                  e.preventDefault();
                  const step = e.key === "ArrowDown" ? 1 : -1;
                  setMenuIndex((i) => (i + step + suggestions.length) % suggestions.length);
                  return;
                }
                if (e.key === "Tab") {
                  e.preventDefault();
                  setInput(`/${current.name}${current.needsArg ? " " : ""}`);
                  return;
                }
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  pick(current);
                  return;
                }
                if (e.key === "Escape") {
                  setMenuDismissed(true);
                  return;
                }
              }
              if (e.key === "Escape") setShowHelp(false);
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
          {sending ? (
            // A stop *request*: the turn ends on its own stream, which then
            // closes normally, so the partial answer stays on screen.
            <Button variant="outline" onClick={() => void stop()} disabled={stopping}>
              {stopping ? "Stopping…" : "Stop"}
            </Button>
          ) : (
            <Button onClick={() => submit()} disabled={!input.trim()}>
              Send
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

/** Tool cards, with each subagent's calls nested under its delegation call. */
function ToolCalls({ calls }: { calls: ToolCallView[] }) {
  const { top, children } = nestCalls(calls);
  return (
    <>
      {top.map((t, i) => (
        <ToolCallCard key={t.id ?? i} call={t} nested={children.get(t.id)} />
      ))}
    </>
  );
}

function Bubble({
  role,
  text,
  blocks,
  citations,
  guardrails,
  assistantId,
  run,
  working = false,
}: {
  role: string;
  text: string;
  /** A live turn that has not streamed any text yet. */
  working?: boolean;
  blocks?: MessageBlock[];
  citations?: Citation[];
  /** A live turn's findings; saved ones come from `blocks`. */
  guardrails?: GuardrailFinding[];
  assistantId: string;
  run?: { conversationId: string; messageId: string };
}) {
  const [active, setActive] = useState<number | null>(null);
  const split = splitBlocks(blocks);
  // Live turns pass citations in directly (they arrive as SSE events, before
  // the message has been re-fetched); persisted ones carry them in blocks.
  const cites = citations ?? split.cites;
  const guards = guardrails ?? split.guards;
  const isAssistant = role === "assistant";

  return (
    <div className={cn("flex", role === "user" ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] space-y-2 rounded-lg px-3 py-2 text-sm",
          role === "user" ? "bg-primary text-primary-foreground" : "bg-muted",
        )}
      >
        {isAssistant && <ToolCalls calls={split.tools} />}
        {isAssistant && <GuardrailNotes items={guards} />}
        {isAssistant && working && !text ? (
          <TypingDots />
        ) : isAssistant ? (
          // Answers are Markdown (headings, lists, tables, code); a user's own
          // message is shown exactly as typed.
          <Markdown text={text} citations={cites} activeMarker={active} onFocus={setActive} />
        ) : (
          <div className="whitespace-pre-wrap">{text}</div>
        )}
        {isAssistant && cites.length > 0 && (
          <SourcesPanel
            citations={cites}
            assistantId={assistantId}
            activeMarker={active}
            onFocus={setActive}
          />
        )}
        {run && <RunDetails conversationId={run.conversationId} messageId={run.messageId} />}
      </div>
    </div>
  );
}

/** How a turn that didn't finish normally is shown (task 5.4): a refusal is
 *  the model declining, not an error, and a failure that may pass on its own
 *  offers to send the message again. */
function TurnError({
  message,
  failure,
  onRetry,
}: {
  message: string;
  failure: { code: string; retryable: boolean } | null;
  onRetry?: () => void;
}) {
  const refused = failure?.code === "refused";
  return (
    <div
      className={cn(
        "flex items-center gap-3 text-sm",
        refused ? "text-muted-foreground" : "text-destructive",
      )}
    >
      <p>{message}</p>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
