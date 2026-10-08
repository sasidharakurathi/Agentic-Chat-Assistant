"use client";

import { MessagesSquare, Plus, X } from "lucide-react";
import { use, useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";

import { ChatThread } from "@/components/chat/ChatThread";
import { canAct } from "@/components/chat/conversation-access";
import { ConversationList } from "@/components/chat/ConversationList";
import { failureText } from "@/components/chat/error-text";
import { LoadFailed, loadFailure, type LoadFailure } from "@/components/load-state";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useConfirm, usePrompt } from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Loading } from "@/components/ui/loading";
import { BackLink } from "@/components/ui/page-header";
import { assistants, conversations, type Conversation } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function ChatPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { user } = useAuth();
  const [name, setName] = useState("");
  const [rows, setRows] = useState<Conversation[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  /** Below 768px the list lives in a sheet. */
  const [listOpen, setListOpen] = useState(false);
  /** The sheet's trigger sits in the thread header, and the thread remounts
   *  when another conversation is picked, so it is found again by id. */
  const listTriggerId = useId();
  const listTrigger = useCallback(() => document.getElementById(listTriggerId), [listTriggerId]);

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

  const [error, setError] = useState<string | null>(null);

  const loadMore = useCallback(async () => {
    if (!more) return;
    setLoadingMore(true);
    try {
      const page = await conversations.list(id, more);
      setRows((r) => {
        const seen = new Set(r.map((c) => c.id));
        return [...r, ...page.items.filter((c) => !seen.has(c.id))];
      });
      setMore(page.next_cursor);
    } catch (err) {
      setError(failureText("Couldn't load older conversations.", err));
    } finally {
      setLoadingMore(false);
    }
  }, [id, more]);

  useEffect(() => {
    void load();
  }, [load]);

  const newChat = useCallback(async () => {
    setError(null);
    try {
      const c = await conversations.create(id);
      setRows((r) => [c, ...r]);
      setActive(c.id);
      setListOpen(false);
    } catch (err) {
      setError(failureText("Couldn't start a new conversation.", err));
    }
  }, [id]);

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
        setError(failureText("Couldn't rename the conversation.", err));
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
        setError(failureText("Couldn't archive the conversation.", err));
      }
    },
    [rows, active, confirm],
  );

  const current = rows.find((r) => r.id === active);

  if (failure) return <LoadFailed failure={failure} what="assistant" />;
  if (loading) {
    return (
      <div className="px-4 py-6 md:px-8">
        <Loading what="conversations" />
      </div>
    );
  }

  const list = (headerAction?: ReactNode) => (
    <ConversationList
      headerAction={headerAction}
      assistantId={id}
      name={name}
      rows={rows}
      userId={user?.id ?? null}
      active={active}
      more={Boolean(more)}
      loadingMore={loadingMore}
      error={error}
      onSelect={(cid) => {
        setActive(cid);
        setListOpen(false);
      }}
      onNewChat={() => void newChat()}
      onRename={(c) => void rename(c)}
      onArchive={(c) => void archive(c)}
      onLoadMore={() => void loadMore()}
    />
  );

  return (
    // Below 768px the app's 52px top bar sits above this page.
    <div className="flex h-[calc(100dvh-52px)] min-h-0 md:h-dvh">
      <aside
        aria-label="Conversations"
        className="border-border bg-background hidden w-[272px] shrink-0 flex-col border-r md:flex"
      >
        {list()}
      </aside>
      <ListSheet open={listOpen} onClose={() => setListOpen(false)} returnFocus={listTrigger}>
        {list(
          <Button
            variant="ghost"
            size="icon"
            aria-label="Close conversations"
            onClick={() => setListOpen(false)}
          >
            <X aria-hidden />
          </Button>,
        )}
      </ListSheet>

      <div className="flex min-w-0 flex-1 flex-col">
        {error && (
          // The list (and its error) is in a closed sheet on phones.
          <Alert className="mx-4 mt-3 md:hidden">{error}</Alert>
        )}
        <div className="min-h-0 flex-1">
          {active ? (
            <ChatThread
              key={active}
              conversationId={active}
              assistantId={id}
              assistantName={name}
              title={current?.title ?? "Conversation"}
              readOnly={!canAct(current, user?.id)}
              headerLeading={
                <Button
                  id={listTriggerId}
                  variant="ghost"
                  size="icon"
                  className="-ml-2 md:hidden"
                  aria-label="Conversations"
                  aria-haspopup="dialog"
                  aria-expanded={listOpen}
                  onClick={() => setListOpen(true)}
                >
                  <MessagesSquare aria-hidden />
                </Button>
              }
              onNewChat={() => void newChat()}
              onRename={(title) => current && void rename(current, title)}
              onArchive={() => current && void archive(current)}
              onTitle={(title) =>
                setRows((r) => r.map((x) => (x.id === active ? { ...x, title } : x)))
              }
              onRunning={(running) =>
                setRows((r) => r.map((x) => (x.id === active ? { ...x, running } : x)))
              }
            />
          ) : (
            <div className="h-full overflow-y-auto px-4 py-6 md:px-8">
              <h1 className="sr-only">{name ? `${name} chat` : "Chat"}</h1>
              <BackLink
                href={`/assistants/${id}/build`}
                label={name ? `${name} settings` : "Assistant settings"}
                className="mb-4 md:hidden"
              />
              <EmptyState
                className="mx-auto mt-[10vh] w-full max-w-[560px]"
                headingLevel={2}
                title={name ? `Chat with ${name}` : "Start a conversation"}
                description="Ask it anything. Every answer shows the steps it took, what it cost and the sources it used."
                action={
                  <Button onClick={() => void newChat()}>
                    <Plus aria-hidden />
                    Start a conversation
                  </Button>
                }
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** The conversation list as a left sheet on phones: a native modal dialog,
 *  so focus is held inside, Esc closes it and focus returns to the button
 *  that opened it. Picking a conversation replaces that button with the new
 *  thread's own, so `returnFocus` finds the one that is there now. */
function ListSheet({
  open,
  onClose,
  returnFocus,
  children,
}: {
  open: boolean;
  onClose: () => void;
  returnFocus?: () => HTMLElement | null;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open) {
      if (!el.open) {
        returnTo.current = document.activeElement as HTMLElement | null;
        el.showModal();
      }
      return;
    }
    // Closed by a button, Esc or the scrim: focus goes back where it was,
    // or, when that element left the page with the old thread, to what
    // replaced it (after this frame, once the new thread is laid out).
    if (!el.open) return;
    el.close();
    const before = returnTo.current;
    returnTo.current = null;
    if (before?.isConnected) {
      before.focus();
      return;
    }
    const frame = requestAnimationFrame(() => returnFocus?.()?.focus());
    return () => cancelAnimationFrame(frame);
  }, [open, returnFocus]);

  // Growing past 768px swaps the sheet for the side column.
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    const onChange = () => {
      if (mq.matches) onClose();
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [onClose]);

  return (
    <dialog
      ref={ref}
      aria-label="Conversations"
      onClose={onClose}
      // Esc: close through state, so focus handling always runs.
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      className="bg-background text-foreground border-border shadow-float backdrop:bg-scrim open:animate-sheet-in fixed inset-y-0 left-0 m-0 h-dvh max-h-none w-[min(20rem,calc(100vw-3rem))] max-w-none border-r p-0"
    >
      {open && children}
    </dialog>
  );
}
