"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ToolCallCard, type ToolCallView } from "@/components/chat/ToolCallCard";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { conversations, streamMessage, type ChatMessage } from "@/lib/api";
import { cn } from "@/lib/utils";

type Live = { text: string; tools: ToolCallView[] };

export function ChatThread({ conversationId }: { conversationId: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [live, setLive] = useState<Live | null>(null);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const loadMessages = useCallback(async () => {
    const c = await conversations.get(conversationId);
    setMessages(c.messages);
  }, [conversationId]);

  useEffect(() => {
    setLive(null);
    setPendingUser(null);
    setError(null);
    void loadMessages();
  }, [loadMessages]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, live]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setError(null);
    setPendingUser(text);
    setLive({ text: "", tools: [] });
    setSending(true);
    try {
      await streamMessage(conversationId, text, (e) => {
        setLive((prev) => {
          if (!prev) return prev;
          switch (e.type) {
            case "token":
              return { ...prev, text: prev.text + e.text };
            case "tool_call":
              return {
                ...prev,
                tools: [...prev.tools, { id: e.id, name: e.name, input: e.input }],
              };
            case "tool_result":
              return {
                ...prev,
                tools: prev.tools.map((t) =>
                  t.id === e.id ? { ...t, status: e.status, output: e.output } : t,
                ),
              };
            default:
              return prev;
          }
        });
        if (e.type === "error") setError(e.message);
      });
      await loadMessages();
    } catch (err) {
      setError(err instanceof Error ? err.message : "stream failed");
    } finally {
      setLive(null);
      setPendingUser(null);
      setSending(false);
    }
  }, [conversationId, input, sending, loadMessages]);

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="flex-1 space-y-4 overflow-auto p-6">
        {messages.map((m) => (
          <Bubble key={m.id} role={m.role} text={m.content} blocks={m.blocks} />
        ))}
        {pendingUser && <Bubble role="user" text={pendingUser} />}
        {live && (
          <div className="space-y-2">
            {live.tools.map((t) => (
              <ToolCallCard key={t.id} call={t} />
            ))}
            <Bubble role="assistant" text={live.text || "…"} />
          </div>
        )}
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
          <Button onClick={() => void send()} disabled={sending || !input.trim()}>
            Send
          </Button>
        </div>
      </div>
    </div>
  );
}

function Bubble({ role, text, blocks }: { role: string; text: string; blocks?: unknown[] }) {
  const tools = (blocks ?? []) as ToolCallView[];
  return (
    <div className={cn("flex", role === "user" ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] space-y-2 rounded-lg px-3 py-2 text-sm",
          role === "user" ? "bg-primary text-primary-foreground" : "bg-muted",
        )}
      >
        {role === "assistant" && tools.map((t, i) => <ToolCallCard key={t.id ?? i} call={t} />)}
        <div className="whitespace-pre-wrap">{text}</div>
      </div>
    </div>
  );
}
