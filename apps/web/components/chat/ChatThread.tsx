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

import { AnswerFeedback, SolvedPrompt, type Rated } from "@/components/chat/AnswerFeedback";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import {
  AttachButton,
  MAX_IMAGES,
  MessageImages,
  ScreenshotTray,
  useScreenshots,
  type PendingShot,
} from "@/components/chat/Attachments";
import { asSentence, failureText } from "@/components/chat/error-text";
import { GuardrailNotes } from "@/components/chat/GuardrailNotes";
import { NOTICE_REQUIRED_EVENT } from "@/components/privacy/NoticeGate";
import { Markdown } from "@/components/chat/Markdown";
import { RunDetails } from "@/components/chat/RunDetails";
import { SourcesPanel } from "@/components/chat/SourcesPanel";
import { ToolSteps, type ToolCallView } from "@/components/chat/ToolCallCard";
import { RunningBadge, TypingDots } from "@/components/chat/TypingDots";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";
import { fieldClasses } from "@/components/ui/input";
import { Lamp } from "@/components/ui/lamp";
import { useToast } from "@/components/ui/toast";
import { imageIds, splitBlocks } from "@/lib/message-blocks";
import { looksLikeSecret } from "@/lib/typed-secrets";
import {
  ApiError,
  approvals as approvalsApi,
  conversations,
  type Approval,
  streamMessage,
  watchTurn,
  type ChatEvent,
  type ChatMessage,
  type Citation,
  type MessageBlock,
  memories as memoriesApi,
  type MemoryFile,
  type GuardrailFinding,
  type TurnOutcome,
} from "@/lib/api";
import { isOpen, pollDelayMs, titleWithPending } from "@/lib/approvals";
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
import {
  RECONNECT_DELAYS_MS,
  giveBack,
  keptAlready,
  limitBack,
  pause,
  retryAfterOf,
  unreachable,
} from "@/lib/turn-resume";
import { useAuth } from "@/lib/auth";
import { isEmployee } from "@/lib/roles";
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
 *  the running lamp and Stop while an answer is being written. Someone
 *  else's conversation (`readOnly`) has no composer: a note says why. */
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
  onRunning,
  readOnly = false,
  readOnlyNote,
  starters = [],
  newerVersion = false,
  headerTrailing,
  feedback: feedbackOn = true,
}: {
  /** Thumbs and "Did this solve it?" (Phase 8.8): Chat's, not a preview's. */
  feedback?: boolean;
  /** A control after the title, e.g. the assistant's card (Phase 8.7). */
  headerTrailing?: ReactNode;
  /** A newer version answers in Chat than the one this conversation is on
   *  (Phase 8.6): said, with a way to start a new conversation. */
  newerVersion?: boolean;
  /** The assistant's conversation starters (Phase 8.5): offered while the
   *  conversation is empty. */
  starters?: string[];
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
  /** An answer started or stopped being written here: for the list's lamp. */
  onRunning?: (running: boolean) => void;
  /** Started by someone else: it can be read, not replied in, stopped or
   *  renamed (Phase 7a.4). */
  readOnly?: boolean;
  /** Why there is no composer, when it is not someone else's conversation:
   *  the assistant is paused, or nothing is approved yet (Phase 7b.1). */
  readOnlyNote?: string;
}) {
  // An employee's view (Phase 8.1): requests wait for an admin, and run
  // details are the Studio's.
  const auth = useAuth();
  const employee = isEmployee(auth.activeRole);
  const me = auth.user?.id;
  // What a conversation cost is the Studio's too (Phase 8.6).
  const commands = employee ? SLASH_COMMANDS.filter((c) => c.name !== "cost") : SLASH_COMMANDS;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [live, setLive] = useState<Live | null>(null);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** The typed failure behind `error`, when the turn reported one. */
  const [failure, setFailure] = useState<Failure | null>(null);
  /** The connection dropped mid-answer and is being picked up (Phase 8.6). */
  const [reconnecting, setReconnecting] = useState(false);
  /** It couldn't be picked up: the answer carries on on the server, and
   *  reloading the conversation shows it. */
  const [lost, setLost] = useState(false);
  /** What was last sent, for Try again: the failed message itself, not the
   *  last one the server kept (Phase 8.6). */
  const lastAttempt = useRef<string | null>(null);
  /** Your thumbs on each answer, and "solved" (Phase 8.8). */
  const [rated, setRated] = useState<Record<string, Rated>>({});
  const [solved, setSolved] = useState<boolean | null>(null);
  /** Budget warnings from the last turn. */
  const [budgetNotes, setBudgetNotes] = useState<string[]>([]);
  const [input, setInput] = useState("");
  // Requests are kept outside `live` on purpose (Phase 9.1): they outlive the
  // answer that raised them by hours, and must be there after a reload,
  // which `live` never is. Only the open ones show as cards; how one ended
  // is posted into the thread.
  const [requests, setRequests] = useState<Approval[]>([]);
  const openIds = useRef<Set<string>>(new Set());
  const toast = useToast();
  // Screenshots (Phase 9.6): uploaded as they are picked or pasted, sent
  // with the next message. `pendingShots`: the sent ones' previews, shown
  // in your bubble until the saved message replaces it.
  const shotProblem = useCallback((message: string) => toast(message, "error"), [toast]);
  const screenshots = useScreenshots(conversationId, shotProblem);
  const [pendingShots, setPendingShots] = useState<string[]>([]);
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
  // The stream being watched. Leaving the conversation lets go of it; the
  // turn itself carries on on the server (QOS-01), and is watched again
  // when the conversation is opened.
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

  const loadMessages = useCallback(async (): Promise<string | null> => {
    const c = await conversations.get(conversationId);
    setMessages(c.messages);
    setRated(
      Object.fromEntries(
        Object.entries(c.feedback ?? {}).map(([k, v]) => [
          k,
          { rating: v.rating ?? null, comment: v.comment ?? "" },
        ]),
      ),
    );
    setSolved(c.solved ?? null);
    setOlder(c.messages_next_cursor);
    // The server's figure: every turn's model spend plus retrieval (embedding
    // and rerank), which the live usage events below never include.
    setSpend({
      cost: Number(c.cost_usd),
      tokensIn: Number(c.token_usage?.in ?? 0),
      tokensOut: Number(c.token_usage?.out ?? 0),
    });
    return c.turn_id ?? null;
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

  // The approval_required event only reaches whoever was watching the
  // stream; a reload or another tab reads them back from the server. When
  // one has ended since the last look, its outcome is in the thread now.
  const loadRequests = useCallback(async () => {
    try {
      const rows = (await approvalsApi.listAll(conversationId)).filter((r) => isOpen(r.status));
      const ids = new Set(rows.map((r) => r.id));
      const ended = [...openIds.current].some((id) => !ids.has(id));
      openIds.current = ids;
      setRequests(rows);
      if (ended) void loadMessages();
    } catch {
      /* a failed poll must not break the thread */
    }
  }, [conversationId, loadMessages]);

  // Looked at again while any is open: soon while one runs, now and then
  // while one waits (an approver may decide hours later).
  useEffect(() => {
    const delay = pollDelayMs(requests.map((r) => r.status));
    if (delay === null) return;
    const timer = setTimeout(() => void loadRequests(), delay);
    return () => clearTimeout(timer);
  }, [requests, loadRequests]);

  useEffect(() => {
    setLive(null);
    setPendingUser(null);
    setPendingShots([]);
    setError(null);
    setFailure(null);
    setRequests([]);
    openIds.current = new Set();
    setSpend(null);
    let gone = false;
    void loadMessages().then((turnId) => {
      // An answer is being written here (sent before a reload, or from
      // another tab): watch it from its start.
      if (turnId && !gone) void watchRef.current(turnId);
    });
    void loadRequests();
    // Switching conversation or unmounting: stop watching. The turn is not
    // stopped by that (QOS-01); it is watched again on coming back.
    return () => {
      gone = true;
      abortRef.current?.abort();
    };
  }, [loadMessages, loadRequests]);

  // While a request waits for the person reading, the tab says so: they
  // may be looking at another tab.
  const waitingOnMe = requests.filter(
    (r) => r.status === "pending" && r.tier === "asker" && r.requested_by === me,
  ).length;
  useEffect(() => {
    document.title = titleWithPending(document.title, waitingOnMe);
  }, [waitingOnMe]);
  useEffect(() => () => void (document.title = titleWithPending(document.title, 0)), []);

  const stop = useCallback(async () => {
    setStopping(true);
    try {
      await conversations.interrupt(conversationId);
    } catch {
      // Letting go of the stream no longer stops a turn, so there is no
      // fallback: say so, and leave Stop there to try again.
      setStopping(false);
      setError("Couldn't stop the answer. Try Stop again.");
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
  }, [messages, live, requests]);

  // Answer text arrives a few characters at a time: it is added once per
  // frame, not once per event, so a long answer doesn't re-render the
  // thread hundreds of times a second (Phase 8.6).
  const textQueue = useRef("");
  const textFrame = useRef<number | null>(null);
  const flushText = useCallback(() => {
    if (textFrame.current !== null) cancelAnimationFrame(textFrame.current);
    textFrame.current = null;
    const more = textQueue.current;
    if (!more) return;
    textQueue.current = "";
    setLive((prev) => (prev ? { ...prev, text: prev.text + more } : prev));
  }, []);
  const queueText = useCallback(
    (more: string) => {
      textQueue.current += more;
      textFrame.current ??= requestAnimationFrame(flushText);
    },
    [flushText],
  );

  /** Watch a turn's events into the live answer, from sending a message
   *  or from opening a conversation that is answering. `watching`: the turn
   *  was started elsewhere, so its events are a replay. A request it stored
   *  is always read back from the server (Phase 9.1), never built from the
   *  event, so a replay can't show one that was already decided.
   *
   *  Phase 8.6: a connection that drops partway is picked up where it left
   *  off (the turn carries on on the server), and `attempt`, the message
   *  being sent, goes back into the box if the server never kept it;
   *  `restore` puts its screenshots back too (Phase 9.6). */
  const follow = useCallback(
    async (
      start: (onEvent: (e: ChatEvent) => void, signal: AbortSignal) => Promise<TurnOutcome>,
      { attempt, restore }: { watching: boolean; attempt?: string; restore?: () => void },
    ) => {
      setFinished(null);
      setError(null);
      setFailure(null);
      setLost(false);
      setReconnecting(false);
      setBudgetNotes([]);
      nearBottom.current = true;
      startedAt.current.clear();
      textQueue.current = "";
      setLive({ text: "", tools: [], citations: [], guardrails: [] });
      setSending(true);
      onRunning?.(true);
      const controller = new AbortController();
      abortRef.current?.abort();
      abortRef.current = controller;
      let failed = false;
      // Ours to give back until the server says it kept it. A message that
      // is only a screenshot is an attempt too: its text is "".
      let kept = attempt === undefined;
      // Sending while an answer is still being written: watch that one.
      let attachAfter = false;
      const onEvent = (e: ChatEvent) => {
        if (e.type === "accepted") kept = true;
        if (e.type === "token" && !e.parent_id) {
          queueText(e.text);
          return;
        }
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
              // Stored by the server (Phase 9.1): read it back with
              // everything its card needs, its editable values included.
              void loadRequests();
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
          // A used-up limit says when it is back, in the reader's own time
          // (the builder budget too, Phase 9.7).
          const back = limitBack(e.code, e.resets_at);
          setError([asSentence(e.message), back].filter(Boolean).join(" "));
          setFailure({
            code: e.code,
            retryable: Boolean(e.retryable),
            retryAfter: e.retry_after_s ?? null,
          });
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
      };
      try {
        let outcome: TurnOutcome | null = null;
        try {
          outcome = await start(onEvent, controller.signal);
        } catch (err) {
          // No answer at all: whether the message got there is found out
          // below. A refusal is an answer, shown as it is.
          if (!unreachable(err) || controller.signal.aborted) throw err;
        }
        // Dropped partway, or never answered: find the turn and watch it
        // again from the last event seen.
        for (const wait of RECONNECT_DELAYS_MS) {
          if (outcome?.ended || controller.signal.aborted) break;
          setReconnecting(true);
          await pause(wait, controller.signal);
          if (controller.signal.aborted) break;
          try {
            const now = await conversations.get(conversationId);
            const turnId = outcome?.turnId ?? now.turn_id ?? null;
            if (!turnId) {
              // Nothing running: it finished meanwhile, or never started.
              kept ||= keptAlready(now.messages, attempt);
              outcome = { turnId: null, ended: true, lastId: null };
              break;
            }
            outcome = await watchTurn(
              conversationId,
              turnId,
              onEvent,
              controller.signal,
              outcome?.lastId ?? null,
            );
          } catch (err) {
            if (err instanceof ApiError && err.code === "turn_not_found") {
              // Finished long enough ago that only the saved answer is left.
              outcome = { turnId: null, ended: true, lastId: null };
              break;
            }
            if (!unreachable(err)) throw err;
          }
        }
        setReconnecting(false);
        flushText();
        if (controller.signal.aborted) {
          // Left: nothing to settle.
        } else if (!outcome?.ended) {
          failed = true;
          setLost(true);
          setError(
            kept
              ? "Lost the connection while the answer was being written. It carries on on the server: reload the conversation to see it."
              : "Couldn't reach the server, so your message may not have been sent. It's back in the box: send it again once you're connected.",
          );
        } else {
          await loadMessages();
        }
      } catch (err) {
        // Our own abort (leaving the conversation) is not an error to show.
        if (!controller.signal.aborted) {
          failed = true;
          attachAfter = err instanceof ApiError && err.code === "turn_in_progress";
          // The notice changed while this was open (Phase 8.7): show it.
          if (err instanceof ApiError && err.code === "notice_required") {
            window.dispatchEvent(new Event(NOTICE_REQUIRED_EVENT));
          }
          setError(
            err instanceof ApiError
              ? asSentence(err.message)
              : "The answer stopped partway. Check your connection, then try again.",
          );
          setFailure(
            err instanceof ApiError
              ? { code: err.code, retryable: err.status === 429, retryAfter: retryAfterOf(err) }
              : null,
          );
        }
      } finally {
        flushText();
        // Only the stream still being watched settles the thread: one let
        // go of (another conversation opened, or this one watched again)
        // must not clear what replaced it.
        if (abortRef.current === controller) {
          abortRef.current = null;
          setLive(null);
          setPendingUser(null);
          setPendingShots([]);
          setSending(false);
          setStopping(false);
          // Let go of on purpose (this conversation was left): the turn is
          // still running on the server, so the list keeps its lamp lit.
          if (!controller.signal.aborted) {
            // Never kept: the text goes back in the box, nothing lost.
            if (failed && !kept && attempt) setInput((box) => giveBack(box, attempt));
            // And its screenshots, which weren't sent either.
            if (failed && !kept) restore?.();
            setReconnecting(false);
            onRunning?.(false);
            // What the turn asked for is stored: read back where each
            // request stands.
            void loadRequests();
            // An error announces itself (role="alert"); a good ending doesn't.
            if (!failed) setFinished("Answer ready");
            if (attachAfter) {
              void loadMessages().then((turnId) => {
                if (turnId) void watchRef.current(turnId);
              });
            }
          }
        }
      }
    },
    [conversationId, loadMessages, loadRequests, onTitle, onRunning, queueText, flushText],
  );

  const restoreShots = screenshots.restore;
  const sendText = useCallback(
    async (text: string, shots: PendingShot[] = []) => {
      if ((!text && shots.length === 0) || sending) return;
      lastAttempt.current = text || null;
      setPendingUser(text);
      setPendingShots(shots.map((s) => s.preview));
      const ids = shots.flatMap((s) => (s.id ? [s.id] : []));
      await follow((onEvent, signal) => streamMessage(conversationId, text, onEvent, signal, ids), {
        watching: false,
        attempt: text,
        restore: shots.length ? () => restoreShots(shots) : undefined,
      });
    },
    [conversationId, sending, follow, restoreShots],
  );

  const watch = useCallback(
    (turnId: string) =>
      follow((onEvent, signal) => watchTurn(conversationId, turnId, onEvent, signal), {
        watching: true,
      }),
    [conversationId, follow],
  );
  // The mount effect runs before `watch` exists in its closure: it reads
  // the current one through this.
  const watchRef = useRef(watch);
  watchRef.current = watch;

  // ── slash commands ──────────────────────────────────────────
  const [menuIndex, setMenuIndex] = useState(0);
  const [menuDismissed, setMenuDismissed] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const suggestions = menuDismissed
    ? []
    : suggestCommands(input).filter((c) => commands.includes(c));

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

  const { take: takeShots, ready: shotsReady, uploading: shotsUploading } = screenshots;
  const submit = useCallback(() => {
    const parsed = parseInput(input);
    if (parsed.kind === "message") {
      if (!parsed.text && shotsReady === 0) return;
      // Kept in the box while an answer is being written: Stop first.
      if (sending) return;
      if (shotsUploading) {
        toast("Wait for the screenshots to finish uploading, then send.");
        return;
      }
      setInput("");
      void sendText(parsed.text, takeShots());
      return;
    }
    if (parsed.kind === "unknown" || !commands.some((c) => c.name === parsed.name)) {
      toast(`Unknown command /${parsed.name}. Type /help to see them all.`);
      return;
    }
    setInput("");
    runCommand(parsed.name, parsed.arg);
  }, [
    input,
    sendText,
    runCommand,
    toast,
    commands,
    sending,
    shotsReady,
    shotsUploading,
    takeShots,
  ]);

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

  const canRate = feedbackOn && !readOnly;
  const secretHint = looksLikeSecret(input);
  const lastAnswer = [...messages].reverse().find((m) => m.role === "assistant")?.id;
  const turns = messages.map((m) =>
    m.role === "user" ? (
      <UserTurn
        key={m.id}
        text={m.content}
        conversationId={conversationId}
        imageIds={imageIds(m.blocks)}
      />
    ) : m.role === "assistant" ? (
      <AssistantTurn
        key={m.id}
        name={who}
        text={m.content}
        blocks={m.blocks}
        assistantId={assistantId}
        // Run details are the Studio's (Phase 8.1): not for an employee.
        run={employee ? undefined : { conversationId, messageId: m.id }}
        footer={
          canRate ? (
            <div className="flex flex-col gap-3">
              <AnswerFeedback
                value={rated[m.id]}
                onChange={async (next) => {
                  const saved = await conversations.rate(
                    conversationId,
                    m.id,
                    next.rating,
                    next.comment,
                  );
                  setRated((r) => ({
                    ...r,
                    [m.id]: { rating: saved.rating ?? null, comment: saved.comment ?? "" },
                  }));
                }}
              />
              {m.id === lastAnswer && !sending && (
                <SolvedPrompt
                  value={solved}
                  onChange={async (yes) => {
                    setSolved((await conversations.solve(conversationId, yes)).solved);
                  }}
                />
              )}
            </div>
          ) : undefined
        }
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
        {headerTrailing}
        {spend && !employee && (
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
          {messages.length === 0 && !live && !readOnly && starters.length > 0 && (
            <div className="flex flex-col gap-2">
              <p className="text-small text-muted-foreground">Try asking</p>
              <div className="flex flex-wrap gap-2">
                {starters.map((s) => (
                  <Button key={s} variant="outline" size="sm" onClick={() => void sendText(s)}>
                    {s}
                  </Button>
                ))}
              </div>
            </div>
          )}
          {turns}
          {pendingUser !== null && (
            <UserTurn text={pendingUser} conversationId={conversationId} previews={pendingShots} />
          )}
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
          {requests.map((r) => (
            <ApprovalCard
              key={`${r.id}:${r.status}:${r.input_sha256 ?? ""}`}
              request={r}
              mine={r.requested_by === me}
              employee={employee}
              onChanged={() => void loadRequests()}
              onGone={(reason) => {
                toast(`This request has moved on. ${asSentence(reason)}`);
                void loadRequests();
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
                      // The message that failed (Phase 8.6), which may be
                      // back in the box; else the last one kept.
                      const text =
                        lastAttempt.current ??
                        [...messages].reverse().find((m) => m.role === "user")?.content;
                      if (!text) return;
                      setInput((box) => (box === text ? "" : box));
                      void sendText(text);
                    }
                  : undefined
              }
              onReload={
                lost && !sending
                  ? () => {
                      setError(null);
                      setLost(false);
                      void loadMessages().then((turnId) => {
                        if (turnId) void watchRef.current(turnId);
                      });
                    }
                  : undefined
              }
            />
          )}
          {newerVersion && !readOnly && onNewChat && (
            <Alert tone="neutral" role="status" className="max-w-[68ch]">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <p className="min-w-0 flex-1">
                  This assistant has been updated. Start a new conversation to use the latest
                  version.
                </p>
                <Button size="sm" variant="outline" onClick={onNewChat}>
                  Start a new conversation
                </Button>
              </div>
            </Alert>
          )}
        </div>
      </div>

      {readOnly ? (
        <div className="border-border bg-background shrink-0 border-t px-4 py-3 md:px-6">
          <div className="mx-auto flex w-full max-w-[72ch] flex-wrap items-center justify-between gap-x-4 gap-y-1">
            <p className="text-small text-muted-foreground">
              {readOnlyNote ?? "Only the person who started this conversation can reply in it."}
            </p>
            <p role="status">{sending ? <RunningBadge label="Answering…" /> : null}</p>
          </div>
        </div>
      ) : (
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
                  {commands.map((c) => (
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
            <ScreenshotTray shots={screenshots.shots} onRemove={screenshots.remove} />
            <div className="flex items-end gap-2">
              <AttachButton
                onFiles={screenshots.add}
                disabled={screenshots.shots.length >= MAX_IMAGES}
              />
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
                onPaste={screenshots.onPaste}
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
                <Button
                  size="lg"
                  className="px-5"
                  onClick={() => submit()}
                  disabled={(!input.trim() && shotsReady === 0) || shotsUploading}
                >
                  Send
                </Button>
              )}
            </div>
            {/* Phase 9.6: a warning, not a block. The server hides it anyway. */}
            <div aria-live="polite">
              {secretHint && (
                <p className="text-small text-muted-foreground mt-2 flex items-center gap-2">
                  <Lamp tone="warning" />
                  This looks like a password or key. It will be hidden before it&apos;s sent.
                </p>
              )}
            </div>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
              {/* One live region, always in the page: a region inserted along
                with its text is often not read out. */}
              <p role="status">
                {sending ? (
                  <RunningBadge
                    label={stopping ? "Stopping…" : reconnecting ? "Reconnecting…" : "Answering…"}
                  />
                ) : finished ? (
                  <span className="sr-only">{finished}</span>
                ) : null}
              </p>
              <p id={hintId} className="text-small text-muted-foreground">
                Enter sends. Shift+Enter adds a line. Type / for commands. Paste a screenshot to
                attach it.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** Your own message, exactly as typed (never rendered as Markdown), on a
 *  muted plate at the right, with any screenshots sent with it above. */
function UserTurn({
  text,
  conversationId,
  imageIds: ids,
  previews,
}: {
  text: string;
  conversationId: string;
  /** Saved screenshots, by attachment id (Phase 9.6). */
  imageIds?: string[];
  /** Screenshots just sent, as local previews. */
  previews?: string[];
}) {
  return (
    <article className="flex flex-col items-end gap-2">
      <h2 className="sr-only">You</h2>
      <MessageImages conversationId={conversationId} ids={ids} previews={previews} />
      {text && (
        <div className="bg-muted text-foreground max-w-[85%] rounded-lg px-4 py-2.5 break-words whitespace-pre-wrap">
          {text}
        </div>
      )}
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
  footer,
}: {
  /** Under a saved answer: its thumbs (Phase 8.8). */
  footer?: ReactNode;
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
      {footer}
    </article>
  );
}

/** A failed turn's code, whether sending again may work, and how long to
 *  wait first (Phase 8.6). */
type Failure = { code: string; retryable: boolean; retryAfter?: number | null };

/** How a turn that didn't finish normally is shown: a refusal is the model
 *  declining, not an error, and a failure that may pass on its own offers
 *  to send the message again, once it is worth it (a countdown when the
 *  server says how long). A lost connection offers to reload instead:
 *  sending again would ask twice. */
function TurnError({
  message,
  failure,
  onRetry,
  onReload,
}: {
  message: string;
  failure: Failure | null;
  onRetry?: () => void;
  onReload?: () => void;
}) {
  const refused = failure?.code === "refused";
  const [wait, setWait] = useState(failure?.retryAfter ?? 0);
  useEffect(() => setWait(failure?.retryAfter ?? 0), [failure]);
  useEffect(() => {
    if (wait <= 0) return;
    const t = setTimeout(() => setWait((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [wait]);
  return (
    <Alert tone={refused ? "neutral" : "destructive"} className="max-w-[68ch]">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <p className="min-w-0 flex-1">{message}</p>
        {onReload && (
          <Button size="sm" variant="outline" onClick={onReload}>
            Reload conversation
          </Button>
        )}
        {onRetry && (
          <Button size="sm" variant="outline" onClick={onRetry} disabled={wait > 0}>
            {wait > 0 ? `Try again in ${wait}s` : "Try again"}
          </Button>
        )}
      </div>
    </Alert>
  );
}
