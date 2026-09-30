"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";

import { ChatThread } from "@/components/chat/ChatThread";
import { LoadFailed, loadFailure, type LoadFailure } from "@/components/load-state";
import { Button, buttonVariants } from "@/components/ui/button";
import { useConfirm, usePrompt } from "@/components/ui/dialog";
import { ApiError, assistants, conversations, type Conversation } from "@/lib/api";
import { cn } from "@/lib/utils";

export default function ChatPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [name, setName] = useState("");
  const [rows, setRows] = useState<Conversation[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState<string | null>(null);

  const [failure, setFailure] = useState<LoadFailure | null>(null);

  const load = useCallback(async () => {
    let a, page;
    try {
      [a, page] = await Promise.all([assistants.get(id), conversations.list(id)]);
    } catch (err) {
      setFailure(loadFailure(err));
      setLoading(false);
      return;
    }
    setName(a.name);
    setRows(page.items);
    setMore(page.next_cursor);
    setActive((cur) => cur ?? page.items[0]?.id ?? null);
    setLoading(false);
  }, [id]);

  const loadMore = useCallback(async () => {
    if (!more) return;
    const page = await conversations.list(id, more);
    setRows((r) => {
      const seen = new Set(r.map((c) => c.id));
      return [...r, ...page.items.filter((c) => !seen.has(c.id))];
    });
    setMore(page.next_cursor);
  }, [id, more]);

  useEffect(() => {
    void load();
  }, [load]);

  const newChat = useCallback(async () => {
    const c = await conversations.create(id);
    setRows((r) => [c, ...r]);
    setActive(c.id);
  }, [id]);

  const [error, setError] = useState<string | null>(null);

  const confirm = useConfirm();
  const askTitle = usePrompt();

  const rename = useCallback(
    async (c: Conversation, preset?: string) => {
      // `/rename <title>` passes the title; the Rename button asks for one.
      const title = (
        preset ??
        (await askTitle({
          title: "Rename conversation",
          label: "Title",
          defaultValue: c.title,
          confirmLabel: "Rename",
        }))
      )?.trim();
      if (!title || title === c.title) return;
      setError(null);
      try {
        const updated = await conversations.rename(c.id, title);
        setRows((r) => r.map((x) => (x.id === c.id ? { ...x, title: updated.title } : x)));
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not rename");
      }
    },
    [askTitle],
  );

  const archive = useCallback(
    async (c: Conversation) => {
      const sure = await confirm({
        title: `Archive "${c.title}"?`,
        description: "It leaves this list and can't be continued.",
        confirmLabel: "Archive",
        destructive: true,
      });
      if (!sure) return;
      setError(null);
      try {
        await conversations.archive(c.id);
        const next = rows.filter((x) => x.id !== c.id);
        setRows(next);
        if (active === c.id) setActive(next[0]?.id ?? null);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not archive");
      }
    },
    [rows, active, confirm],
  );

  const current = rows.find((r) => r.id === active);

  if (failure) return <LoadFailed failure={failure} what="assistant" />;
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
          {error && (
            <p className="text-destructive px-2 py-1 text-xs" role="alert">
              {error}
            </p>
          )}
          {rows.map((c) => (
            <div key={c.id}>
              <button
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
              {c.id === active && (
                <div className="flex gap-3 px-2 pt-0.5 pb-1">
                  <button
                    className="text-muted-foreground hover:text-foreground text-xs"
                    onClick={() => void rename(c)}
                  >
                    Rename
                  </button>
                  <button
                    className="text-muted-foreground hover:text-destructive text-xs"
                    onClick={() => void archive(c)}
                  >
                    Archive
                  </button>
                </div>
              )}
            </div>
          ))}
          {rows.length === 0 && (
            <p className="text-muted-foreground px-2 py-4 text-xs">No conversations yet.</p>
          )}
          {more && (
            <button
              className="text-muted-foreground hover:text-foreground w-full px-2 py-2 text-xs"
              onClick={() => void loadMore()}
            >
              Load older conversations
            </button>
          )}
        </div>
      </aside>
      <div className="min-w-0 flex-1">
        {active ? (
          <ChatThread
            key={active}
            conversationId={active}
            assistantId={id}
            title={current?.title ?? "Conversation"}
            onNewChat={() => void newChat()}
            onRename={(title) => current && void rename(current, title)}
            onArchive={() => current && void archive(current)}
            onTitle={(title) =>
              setRows((r) => r.map((x) => (x.id === active ? { ...x, title } : x)))
            }
          />
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
