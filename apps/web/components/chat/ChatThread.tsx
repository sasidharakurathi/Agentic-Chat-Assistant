"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { ApprovalCard, type PendingApproval } from "@/components/chat/ApprovalCard";
import { RunDetails } from "@/components/chat/RunDetails";
import { CitedText, SourcesPanel } from "@/components/chat/SourcesPanel";
import { ToolCallCard, type ToolCallView } from "@/components/chat/ToolCallCard";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  approvals as approvalsApi,
  conversations,
  streamMessage,
  type ChatMessage,
  type Citation,
  type MessageBlock,
} from "@/lib/api";
import { formatCount, formatUsd } from "@/lib/format";
import { appendSubagentText, nestCalls } from "@/lib/tool-tree";
import { cn } from "@/lib/utils";

type Live = { text: string; tools: ToolCallView[]; citations: Citation[] };
/** What the conversation has cost so far (task 1.8). */
type Spend = { cost: number; tokensIn: number; tokensOut: number };

export function ChatThread({
  conversationId,
  assistantId,
}: {
  conversationId: string;
  assistantId: string;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [live, setLive] = useState<Live | null>(null);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  // Approvals are kept outside `live` on purpose: a pending one must survive
  // the stream ending (a turn can finish denied-by-timeout) and must also be
  // restorable after a reload, which `live` never is.
  const [pending, setPending] = useState<PendingApproval[]>([]);
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
    setPending([]);
    setSpend(null);
    void loadMessages();
    void loadPending();
    // Switching conversation or unmounting: the server records a dropped
    // stream as an aborted turn, so nothing is lost by letting go of it.
    return () => abortRef.current?.abort();
  }, [loadMessages, loadPending]);

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

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setError(null);
    setPendingUser(text);
    setLive({ text: "", tools: [], citations: [] });
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
                    t.id === e.id ? { ...t, status: e.status, output: e.output } : t,
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
              case "citation":
                // Arrives at finalize, after the full answer — a [n] marker only
                // means something once every search in the turn has been seen.
                return { ...prev, citations: [...prev.citations, e as Citation] };
              default:
                return prev;
            }
          });
          if (e.type === "error") setError(e.message);
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
    }
  }, [conversationId, input, sending, loadMessages]);

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
          {sending && <span className="ml-1.5">(this turn is still running)</span>}
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
              text={live.text || "…"}
              citations={live.citations}
              assistantId={assistantId}
            />
          </div>
        )}
        {pending.map((a) => (
          <ApprovalCard
            key={a.approval_id}
            approval={a}
            onDecided={() => setPending((p) => p.filter((x) => x.approval_id !== a.approval_id))}
          />
        ))}
        {error && <p className="text-destructive text-sm">{error}</p>}
      </div>
      <div className="border-border border-t p-4">
        <div className="flex gap-2">
          <Textarea
            rows={2}
            placeholder="Message the assistant…  (Enter to send)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
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
            <Button onClick={() => void send()} disabled={!input.trim()}>
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

/** Split a message's blocks into tool calls and citations.
 *
 *  The tool filter is NEGATIVE on purpose. Messages persisted before task 2.9
 *  have blocks with no `type` key at all, so filtering for `type ===
 *  "tool_call"` would silently stop rendering tool cards across every existing
 *  conversation. "Not a citation" is the condition that stays true for both. */
function splitBlocks(blocks?: MessageBlock[]): { tools: ToolCallView[]; cites: Citation[] } {
  const all = (blocks ?? []) as Array<Record<string, unknown>>;
  return {
    tools: all.filter((b) => b?.type !== "citation") as unknown as ToolCallView[],
    cites: all.filter((b) => b?.type === "citation") as unknown as Citation[],
  };
}

function Bubble({
  role,
  text,
  blocks,
  citations,
  assistantId,
  run,
}: {
  role: string;
  text: string;
  blocks?: MessageBlock[];
  citations?: Citation[];
  assistantId: string;
  run?: { conversationId: string; messageId: string };
}) {
  const [active, setActive] = useState<number | null>(null);
  const split = splitBlocks(blocks);
  // Live turns pass citations in directly (they arrive as SSE events, before
  // the message has been re-fetched); persisted ones carry them in blocks.
  const cites = citations ?? split.cites;
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
        {isAssistant && cites.length > 0 ? (
          <CitedText text={text} citations={cites} activeMarker={active} onFocus={setActive} />
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
