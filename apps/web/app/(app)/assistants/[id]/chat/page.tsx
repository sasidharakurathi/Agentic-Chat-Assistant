"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";

import { ChatThread } from "@/components/chat/ChatThread";
import { Button, buttonVariants } from "@/components/ui/button";
import { assistants, conversations, type Conversation } from "@/lib/api";
import { cn } from "@/lib/utils";

export default function ChatPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [name, setName] = useState("");
  const [rows, setRows] = useState<Conversation[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const [a, list] = await Promise.all([assistants.get(id), conversations.list(id)]);
    setName(a.name);
    setRows(list);
    setActive((cur) => cur ?? list[0]?.id ?? null);
    setLoading(false);
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const newChat = useCallback(async () => {
    const c = await conversations.create(id);
    setRows((r) => [c, ...r]);
    setActive(c.id);
  }, [id]);

  if (loading) return <div className="text-muted-foreground p-10 text-sm">Loading…</div>;

  return (
    <div className="flex h-screen">
      <aside className="border-border flex w-64 shrink-0 flex-col border-r">
        <div className="border-border flex items-center justify-between border-b px-4 py-3">
          <Link href={`/assistants/${id}/build`} className="text-muted-foreground text-sm">
            ← {name}
          </Link>
        </div>
        <div className="p-3">
          <Button size="sm" className="w-full" onClick={() => void newChat()}>
            New chat
          </Button>
        </div>
        <div className="flex-1 space-y-1 overflow-auto px-2">
          {rows.map((c) => (
            <button
              key={c.id}
              onClick={() => setActive(c.id)}
              className={cn(
                "w-full truncate rounded-md px-2 py-1.5 text-left text-sm",
                c.id === active
                  ? "bg-muted font-medium"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {c.title}
            </button>
          ))}
          {rows.length === 0 && (
            <p className="text-muted-foreground px-2 py-4 text-xs">No conversations yet.</p>
          )}
        </div>
      </aside>
      <div className="min-w-0 flex-1">
        {active ? (
          <ChatThread key={active} conversationId={active} />
        ) : (
          <div className="flex h-full items-center justify-center">
            <button className={buttonVariants()} onClick={() => void newChat()}>
              Start a conversation
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
