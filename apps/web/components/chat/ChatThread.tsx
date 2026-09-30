"use client";

import {
  Fragment,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ApprovalCard, type PendingApproval } from "@/components/chat/ApprovalCard";
import { asSentence, failureText } from "@/components/chat/error-text";
import { GuardrailNotes } from "@/components/chat/GuardrailNotes";
import { Markdown } from "@/components/chat/Markdown";
import { RunDetails } from "@/components/chat/RunDetails";
import { SourcesPanel } from "@/components/chat/SourcesPanel";
import { ToolSteps, type ToolCallView } from "@/components/chat/ToolCallCard";
import { RunningBadge, TypingDots } from "@/components/chat/TypingDots";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";
import { fieldClasses } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { splitBlocks } from "@/lib/message-blocks";
import {
  ApiError,
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
import { appendSubagentText } from "@/lib/tool-tree";
import { cn } from "@/lib/utils";

type Live = {
  text: string;
  tools: ToolCallView[];
  citations: Citation[];
  guardrails: GuardrailFinding[];
};
/** What the conversation has cost so far. */
type Spend = { cost: number; tokensIn: number; tokensOut: number };

/** One conversation (docs/DESIGN.md section 7, Chat): a header with the
 *  title and what it has cost, the turns in a centred column (your messages
 *  as muted plates on the right, answers as unboxed reading text with their
 *  tool steps, sources and run details), and the composer, which carries
 *  the running lamp and Stop while an answer is being written. */
export function ChatThread({
  conversationId,
  assistantId,
  assistantName,
  title = "Conversation",
  headerLeading,
  onNewChat,
  onRename,
  onArchive,
  onTitle,
}: {
  conversationId: string;
  assistantId: string;
  /** For the composer's label and placeholder, "Message Shop Helper". */
  assistantName?: string;
  title?: string;
  /** A control before the title, e.g. the conversation list button on phones. */
  headerLeading?: ReactNode;
  /** The first turn named the conversation. */
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
  /** The typed failure behind `error`, when the turn reported one. */
  const [failure, setFailure] = useState<{ code: string; retryable: boolean } | null>(null);
  /** Budget warnings from the last turn. */
  const [budgetNotes, setBudgetNotes] = useState<string[]>([]);
  const [input, setInput] = useState("");
  // Approvals are kept outside `live` on purpose: a pending one must survive
  // the stream ending (a turn can finish denied-by-timeout) and must also be
  // restorable after a reload, which `live` never is.
  const [pending, setPending] = useState<PendingApproval[]>([]);
  const toast = useToast();
  const [stopping, setStopping] = useState(false);
  /** Said once in the composer's status line when a turn ends well, so a
   *  screen reader hears that the answer is there. */
  const [finished, setFinished] = useState<string | null>(null);
  useEffect(() => {
    if (!finished) return;
    const t = setTimeout(() => setFinished(null), 5000);
    return () => clearTimeout(t);
  }, [finished]);
  const scrollRef = useRef<HTMLDivElement>(null);
  // The in-flight stream, so leaving the conversation mid-turn drops it
  // rather than leaving it running in the background.
  const abortRef = useRef<AbortController | null>(null);
  // When each live tool call started, so its step can show how long it took
  // before the saved message (which carries the server's timing) arrives.
  const startedAt = useRef(new Map<string, number>());
  // Whether the reader is at the newest message. New content keeps the view
  // pinned there, but never drags someone who scrolled up to read.
  const nearBottom = useRef(true);

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
  // person, who may be looking at another tab.
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
    if (nearBottom.current) el.scrollTo({ top: el.scrollHeight });
  }, [messages, live, pending]);

  const sendText = useCallback(
    async (text: string) => {
      if (!text || sending) return;
      setFinished(null);
      setError(null);
      setFailure(null);
      setBudgetNotes([]);
      nearBottom.current = true;
      startedAt.current.clear();
      setPendingUser(text);
      setLive({ text: "", tools: [], citations: [], guardrails: [] });
      setSending(true);
      const controller = new AbortController();
      abortRef.current = controller;
      let failed = false;
      try {
        await streamMessage(
          conversationId,
          text,
          (e) => {
            if (e.type === "tool_call") startedAt.current.set(e.id, performance.now());
            const began = e.type === "tool_result" ? startedAt.current.get(e.id) : undefined;
            const took = began !== undefined ? Math.round(performance.now() - began) : null;
            setLive((prev) => {
              if (!prev) return prev;
              switch (e.type) {
                case "token":
                  // A subagent's notes go on its delegation step, not into the
                  // answer.
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
                        ? {
                            ...t,
                            status: e.status,
                            output: e.output,
                            permission: e.permission,
                            duration_ms: took,
                          }
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
              failed = true;
              setError(asSentence(e.message));
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
          failed = true;
          setError(
            err instanceof ApiError
              ? `${asSentence(err.message)} Try sending it again.`
              : "The answer stopped partway. Check your connection, then try again.",
          );
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
        if (!controller.signal.aborted) {
          void loadPending();
          // An error announces itself (role="alert"); a good ending doesn't.
          if (!failed) setFinished("Answer ready");
        }
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
    } catch (err) {
      toast(
        failureText("Couldn't load what this assistant remembers.", err, "Try /memory again."),
        "error",
      );
      return;
    }
    if (files.length === 0) {
      toast("This assistant doesn't remember anything about you yet.");
      return;
    }
    const forget = await confirm({
      title: "What this assistant remembers about you",
      description: (
        <div className="flex max-h-80 flex-col gap-3 overflow-auto">
          {files.map((f) => (
            <div key={f.path}>
              <p className="text-code text-foreground font-mono font-medium break-all">{f.path}</p>
              <pre className="bg-muted border-border text-code text-foreground mt-1 rounded-md border p-3 font-mono break-words whitespace-pre-wrap">
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
    try {
      const { deleted } = await memoriesApi.clear(assistantId);
      toast(`Forgot everything: ${deleted} memory ${deleted === 1 ? "file" : "files"} deleted.`);
    } catch (err) {
      toast(failureText("Couldn't forget what it remembers.", err, "Try /memory again."), "error");
    }
  }, [assistantId, confirm, toast]);

  const runCommand = useCallback(
    (name: SlashCommandName, arg: string) => {
      const busyOnly = new Set<SlashCommandName>(["help", "stop", "cost", "export"]);
      if (sending && !busyOnly.has(name)) {
        toast("Wait for the answer to finish, or type /stop to end it.");
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
            toast("Add the new title after the command, like /rename Refund question.");
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
              ? `This conversation has cost ${formatUsd(spend.cost)} so far, across ${formatCount(spend.tokensIn + spend.tokensOut)} tokens.`
              : "Nothing has been spent in this conversation yet.",
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

  // ── composer ─────────────────────────────────────────────────
  const composerId = useId();
  const hintId = `${composerId}-hint`;
  const listId = `${composerId}-commands`;
  const helpTitleId = `${composerId}-help`;
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const who = assistantName?.trim() || "the assistant";
  const selected = suggestions.length
    ? suggestions[Math.min(menuIndex, suggestions.length - 1)]
    : null;

  // The message box grows with what is typed, up to about eight lines.
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 192)}px`;
  }, [input]);

  const turns = messages.map((m) =>
    m.role === "user" ? (
      <UserTurn key={m.id} text={m.content} />
    ) : m.role === "assistant" ? (
      <AssistantTurn
        key={m.id}
        name={who}
        text={m.content}
        blocks={m.blocks}
        assistantId={assistantId}
        run={{ conversationId, messageId: m.id }}
      />
    ) : (
      // A system or tool message: shown exactly as stored, never as Markdown.
      <p key={m.id} className="text-muted-foreground max-w-[68ch] break-words whitespace-pre-wrap">
        {m.content}
      </p>
    ),
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="border-border flex min-h-14 shrink-0 items-center gap-2 border-b px-4 py-2 md:px-6">
        {headerLeading}
        <h1 className="text-h3 min-w-0 flex-1 truncate font-semibold" title={title}>
          {title}
        </h1>
        {spend && (
          <dl
            className="text-small flex shrink-0 items-baseline gap-4"
            aria-label="Conversation spend"
            title={`${spend.tokensIn.toLocaleString()} tokens in, ${spend.tokensOut.toLocaleString()} out`}
          >
            <div className="flex items-baseline gap-1.5">
              <dt className="text-muted-foreground">Cost</dt>
              <dd className="num text-foreground font-medium">{formatUsd(spend.cost)}</dd>
            </div>
            <div className="hidden items-baseline gap-1.5 sm:flex">
              <dt className="text-muted-foreground">Tokens</dt>
              <dd className="num text-foreground font-medium">
                {formatCount(spend.tokensIn + spend.tokensOut)}
              </dd>
            </div>
          </dl>
        )}
      </header>

      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 96;
        }}
        className="relative min-h-0 flex-1 overflow-y-auto"
      >
        <div className="mx-auto flex w-full max-w-[72ch] flex-col gap-8 px-4 py-6 md:px-6">
          {older && (
            <div className="flex justify-center">
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground hover:text-foreground"
                disabled={loadingOlder}
                onClick={() => void loadOlder()}
              >
                {loadingOlder ? "Loading earlier messages…" : "Load earlier messages"}
              </Button>
            </div>
          )}
          {turns}
          {pendingUser && <UserTurn text={pendingUser} />}
          {live && (
            <AssistantTurn
              name={who}
              text={live.text}
              tools={live.tools}
              working
              citations={live.citations}
              guardrails={live.guardrails}
              assistantId={assistantId}
            />
          )}
          {pending.map((a) => (
            <ApprovalCard
              key={a.approval_id}
              approval={a}
              onDecided={() => setPending((p) => p.filter((x) => x.approval_id !== a.approval_id))}
              onGone={(reason) => {
                setPending((p) => p.filter((x) => x.approval_id !== a.approval_id));
                toast(`This approval is already closed. ${asSentence(reason)}`);
              }}
            />
          ))}
          {budgetNotes.map((note) => (
            // A budget 80% or more used: said, not blocking.
            <Alert key={note} tone="warning" role="status" className="max-w-[68ch]">
              {note}
            </Alert>
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
      </div>

      <div className="border-border bg-background shrink-0 border-t px-4 pt-3 pb-3 md:px-6">
        <div className="relative mx-auto w-full max-w-[72ch]">
          {suggestions.length > 0 && (
            <ul
              id={listId}
              role="listbox"
              aria-label="Commands"
              className="bg-card border-border shadow-float animate-float-in absolute inset-x-0 bottom-full z-20 mb-2 max-h-[50vh] overflow-y-auto rounded-lg border p-1"
            >
              {suggestions.map((c) => (
                <li
                  key={c.name}
                  id={`${listId}-${c.name}`}
                  role="option"
                  aria-selected={c === selected}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    pick(c);
                  }}
                  className={cn(
                    "flex min-h-9 cursor-pointer flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded-md px-2.5 py-1.5",
                    c === selected ? "bg-muted" : "hover:bg-muted",
                  )}
                >
                  <span className="text-code font-mono">/{c.name}</span>
                  {c.args && (
                    <span className="text-code text-muted-foreground font-mono">{c.args}</span>
                  )}
                  <span className="text-small text-muted-foreground sm:ml-auto">
                    {c.description}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {showHelp && (
            <section
              aria-labelledby={helpTitleId}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  setShowHelp(false);
                  textareaRef.current?.focus();
                }
              }}
              className="bg-card border-border shadow-float animate-float-in absolute inset-x-0 bottom-full z-20 mb-2 max-h-[60vh] overflow-y-auto rounded-lg border p-4"
            >
              <div className="mb-3 flex items-center justify-between gap-2">
                <h2 id={helpTitleId} className="text-h4 font-semibold">
                  Commands
                </h2>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setShowHelp(false);
                    textareaRef.current?.focus();
                  }}
                >
                  Close
                </Button>
              </div>
              <dl className="grid gap-x-4 gap-y-1.5 sm:grid-cols-[auto_1fr]">
                {SLASH_COMMANDS.map((c) => (
                  <Fragment key={c.name}>
                    <dt className="text-code font-mono">
                      /{c.name}
                      {c.args ? ` ${c.args}` : ""}
                    </dt>
                    <dd className="text-muted-foreground mb-1.5 sm:mb-0">{c.description}</dd>
                  </Fragment>
                ))}
              </dl>
              <p className="text-small text-muted-foreground mt-3">
                Start a message with // to send text that begins with a slash.
              </p>
            </section>
          )}

          <label htmlFor={composerId} className="sr-only">
            Message {who}
          </label>
          <div className="flex items-end gap-2">
            <textarea
              ref={textareaRef}
              id={composerId}
              rows={1}
              placeholder={`Message ${who}`}
              aria-describedby={hintId}
              // A text box with a command list: announced as a combobox, so
              // the open list and the highlighted command are read out.
              role="combobox"
              aria-expanded={suggestions.length > 0}
              aria-autocomplete="list"
              aria-controls={suggestions.length > 0 ? listId : undefined}
              aria-activedescendant={selected ? `${listId}-${selected.name}` : undefined}
              className={cn(
                fieldClasses,
                "block max-h-48 min-h-11 resize-none px-3 py-[11px] leading-[20px]",
              )}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                setMenuIndex(0);
                setMenuDismissed(false);
                setShowHelp(false);
              }}
              onKeyDown={(e) => {
                if (suggestions.length > 0 && selected) {
                  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                    e.preventDefault();
                    const step = e.key === "ArrowDown" ? 1 : -1;
                    setMenuIndex((i) => (i + step + suggestions.length) % suggestions.length);
                    return;
                  }
                  if (e.key === "Tab") {
                    e.preventDefault();
                    setInput(`/${selected.name}${selected.needsArg ? " " : ""}`);
                    return;
                  }
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    pick(selected);
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
              <Button
                variant="outline"
                size="lg"
                className="px-4"
                onClick={() => void stop()}
                disabled={stopping}
              >
                {stopping ? "Stopping…" : "Stop"}
              </Button>
            ) : (
              <Button size="lg" className="px-5" onClick={() => submit()} disabled={!input.trim()}>
                Send
              </Button>
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
            {/* One live region, always in the page: a region inserted along
                with its text is often not read out. */}
            <p role="status">
              {sending ? (
                <RunningBadge label={stopping ? "Stopping…" : "Answering…"} />
              ) : finished ? (
                <span className="sr-only">{finished}</span>
              ) : null}
            </p>
            <p id={hintId} className="text-small text-muted-foreground">
              Enter sends. Shift+Enter adds a line. Type / for commands.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Your own message, exactly as typed (never rendered as Markdown), on a
 *  muted plate at the right. */
function UserTurn({ text }: { text: string }) {
  return (
    <article className="flex flex-col items-end">
      <h2 className="sr-only">You</h2>
      <div className="bg-muted text-foreground max-w-[85%] rounded-lg px-4 py-2.5 break-words whitespace-pre-wrap">
        {text}
      </div>
    </article>
  );
}

/** An answer: its tool steps, what the guardrails did, the answer itself as
 *  reading text, the sources it cites and, once saved, Run details. */
function AssistantTurn({
  name,
  text,
  tools,
  blocks,
  citations,
  guardrails,
  assistantId,
  run,
  working = false,
}: {
  name: string;
  text: string;
  /** A live turn's calls; saved ones come from `blocks`. */
  tools?: ToolCallView[];
  /** A live turn, still streaming. */
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
  const calls = tools ?? split.tools;

  return (
    <article className="flex min-w-0 flex-col gap-4">
      <h2 className="sr-only">{name}</h2>
      {calls.length > 0 && <ToolSteps calls={calls} live={working} />}
      <GuardrailNotes items={guards} />
      {working && !text ? (
        <TypingDots />
      ) : (
        // Answers are Markdown (headings, lists, tables, code).
        <Markdown text={text} citations={cites} activeMarker={active} onFocus={setActive} />
      )}
      {cites.length > 0 && (
        <SourcesPanel
          citations={cites}
          assistantId={assistantId}
          activeMarker={active}
          onFocus={setActive}
        />
      )}
      {run && <RunDetails conversationId={run.conversationId} messageId={run.messageId} />}
    </article>
  );
}

/** How a turn that didn't finish normally is shown: a refusal is the model
 *  declining, not an error, and a failure that may pass on its own offers
 *  to send the message again. */
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
    <Alert tone={refused ? "neutral" : "destructive"} className="max-w-[68ch]">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <p className="min-w-0 flex-1">{message}</p>
        {onRetry && (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Try again
          </Button>
        )}
      </div>
    </Alert>
  );
}
