"use client";

import { MessagesSquare, Plus, X } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  use,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { AssistantAbout } from "@/components/chat/AssistantAbout";
import { ChatThread } from "@/components/chat/ChatThread";
import { canAct } from "@/components/chat/conversation-access";
import { ConversationList } from "@/components/chat/ConversationList";
import { ExportButton } from "@/components/chat/ExportButton";
import { HandoffButton } from "@/components/chat/HandoffButton";
import { ShareButton } from "@/components/chat/ShareButton";
import { failureText } from "@/components/chat/error-text";
import { LoadFailed, loadFailure, type LoadFailure } from "@/components/load-state";
import { Alert } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { useConfirm, usePrompt } from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Loading } from "@/components/ui/loading";
import { BackLink } from "@/components/ui/page-header";
import {
  chat,
  conversations,
  type ChatAssistant,
  type ChatMode,
  type Conversation,
} from "@/lib/api";
import {
  canEditAssistant,
  chatUnavailable,
  isAdminRole,
  previewHref,
} from "@/lib/assistant-lifecycle";
import { useAuth } from "@/lib/auth";
import { hasReadReason, setReadReason } from "@/lib/read-reasons";
import { isEmployee } from "@/lib/roles";
import { cn } from "@/lib/utils";

export default function ChatPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { user, activeRole } = useAuth();
  // Admins may list everyone's conversations (Phase 7b.2); the rest see
  // only their own, which is all the server returns them.
  const admin = isAdminRole(activeRole);
  const employee = isEmployee(activeRole);
  const [everyone, setEveryone] = useState(false);
  // `?preview=1` is the Studio's test chat on the draft; `&version=N` on one
  // version, for the admin reviewing it (Phase 7b.1). Without it, this is
  // Chat: it answers only from the approved version.
  const search = useSearchParams();
  const previewing = search.get("preview") === "1";
  const pinned = Number(search.get("version")) || undefined;
  const mode = useMemo<ChatMode>(
    () => ({ preview: previewing, version: pinned }),
    [previewing, pinned],
  );
  // The card, not the Studio's record (Phase 8.1): the same for everyone,
  // employees included, and the server says who may edit.
  const [assistant, setAssistant] = useState<ChatAssistant | null>(null);
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
  /** The assistant is gone for you (withdrawn, or you left its audience)
   *  but a conversation of yours is named: it stays readable (Phase 8.6). */
  const [orphan, setOrphan] = useState<Conversation | null>(null);

  const load = useCallback(async () => {
    let a, page;
    try {
      [a, page] = await Promise.all([
        chat.assistant(id),
        conversations.list(id, null, mode, everyone),
      ]);
    } catch (err) {
      const why = loadFailure(err);
      const named = search.get("conversation");
      if (why.notFound && named) {
        try {
          setOrphan(await conversations.get(named));
          setLoading(false);
          return;
        } catch {
          // Not readable either: the plain not-found below.
        }
      }
      setFailure(why);
      setLoading(false);
      return;
    }
    setAssistant(a);
    setName(a.name);
    setRows(page.items);
    setMore(page.next_cursor);
    // A conversation named in the address (from Chat's list, or an admin's
    // queue, which asked for the reason already); else your own most
    // recent one: someone else's needs a reason first.
    const named = search.get("conversation");
    setActive(
      (cur) =>
        cur ??
        (named && (page.items.some((c) => c.id === named) || hasReadReason(named))
          ? named
          : null) ??
        page.items.find((c) => c.created_by === user?.id)?.id ??
        null,
    );
    setLoading(false);
  }, [id, mode, everyone, user?.id, search]);

  const [error, setError] = useState<string | null>(null);

  const loadMore = useCallback(async () => {
    if (!more) return;
    setLoadingMore(true);
    try {
      const page = await conversations.list(id, more, mode, everyone);
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
  }, [id, more, mode, everyone]);

  useEffect(() => {
    void load();
  }, [load]);

  const newChat = useCallback(async () => {
    setError(null);
    try {
      const c = await conversations.create(id, undefined, mode);
      setRows((r) => [c, ...r]);
      setActive(c.id);
      setListOpen(false);
    } catch (err) {
      setError(failureText("Couldn't start a new conversation.", err));
    }
  }, [id, mode]);

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
      // Phase 8.8: said as it is. Erasing comes with the retention rules.
      const sure = await confirm({
        title: `Remove "${c.title}" from your history?`,
        description:
          "It leaves your lists and search. Your company keeps it, and admins can still open it (each time recorded). You can put it back from Removed in Chat.",
        confirmLabel: "Remove",
      });
      if (!sure) return;
      setError(null);
      try {
        await conversations.archive(c.id);
        const next = rows.filter((x) => x.id !== c.id);
        setRows(next);
        if (active === c.id) setActive(next[0]?.id ?? null);
      } catch (err) {
        setError(failureText("Couldn't remove the conversation.", err));
      }
    },
    [rows, active, confirm],
  );

  /** Someone else's conversation opens only once the admin says why: each
   *  read of it is recorded with that reason (Phase 7b.2). */
  const open = useCallback(
    async (cid: string) => {
      const c = rows.find((r) => r.id === cid);
      if (c && c.created_by !== user?.id && !hasReadReason(cid)) {
        const reason = (
          await askTitle({
            title: "Open someone else's conversation?",
            description:
              "Conversations are private. Admins can open any, and each opening is recorded in the audit log with your reason.",
            label: "Reason",
            placeholder: "e.g. Ticket 4411: wrong refund amount",
            confirmLabel: "Open",
          })
        )?.trim();
        if (!reason) return;
        setReadReason(cid, reason);
      }
      setActive(cid);
      setListOpen(false);
    },
    [rows, user?.id, askTitle],
  );

  const current = rows.find((r) => r.id === active);
  const editor = canEditAssistant(assistant, user?.id, activeRole);
  // Chat refuses while nothing is approved or an admin has paused it; a
  // preview runs regardless, unless it is paused.
  const unavailable = assistant
    ? previewing
      ? assistant.status === "archived"
        ? chatUnavailable(assistant)
        : null
      : chatUnavailable(assistant)
    : null;

  const toChat = { href: "/chat", label: "Back to Chat" };
  const fullHeight = employee ? "h-[calc(100dvh-52px)]" : "h-[calc(100dvh-52px)] md:h-dvh";
  if (failure) {
    return <LoadFailed failure={failure} what="assistant" back={employee ? toChat : undefined} />;
  }
  if (orphan) {
    return (
      <div className={cn(fullHeight, "min-h-0")}>
        <ChatThread
          conversationId={orphan.id}
          assistantId={id}
          title={orphan.title}
          readOnly
          readOnlyNote="This assistant isn't available to you any more. You can still read this conversation; to ask something new, pick another assistant in Chat."
          headerLeading={
            <Link href="/chat" className={buttonVariants({ variant: "ghost", size: "sm" })}>
              Chat
            </Link>
          }
        />
      </div>
    );
  }
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
      onSelect={(cid) => void open(cid)}
      scope={
        admin && !previewing
          ? {
              everyone,
              onChange: (v) => {
                setEveryone(v);
                setActive(null);
                setLoading(true);
              },
            }
          : undefined
      }
      onNewChat={() => void newChat()}
      onRename={(c) => void rename(c)}
      onArchive={(c) => void archive(c)}
      onLoadMore={() => void loadMore()}
    />
  );

  return (
    // Below 768px the app's 52px top bar sits above this page; an employee's
    // bar is there at every width (Phase 8.6).
    <div className={cn("flex min-h-0", fullHeight)}>
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
        {previewing && <PreviewBanner assistantId={id} version={pinned} />}
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
              starters={previewing ? [] : (assistant?.starters ?? [])}
              newerVersion={
                !previewing &&
                Boolean(current?.assistant_version_id) &&
                Boolean(assistant?.current_version_id) &&
                current?.assistant_version_id !== assistant?.current_version_id
              }
              title={current?.title ?? "Conversation"}
              readOnly={!canAct(current, user?.id) || Boolean(unavailable)}
              readOnlyNote={unavailable ?? undefined}
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
              headerTrailing={
                previewing ? undefined : (
                  <>
                    {canAct(current, user?.id) && <HandoffButton conversationId={active} />}
                    {canAct(current, user?.id) && <ShareButton conversationId={active} />}
                    <ExportButton conversationId={active} />
                    <AssistantAbout assistantId={id} />
                  </>
                )
              }
              feedback={!previewing}
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
              {employee ? (
                <BackLink href="/chat" label="Chat" className="mb-4 md:hidden" />
              ) : (
                <BackLink
                  href={`/assistants/${id}/build`}
                  label={name ? `${name} settings` : "Assistant settings"}
                  className="mb-4 md:hidden"
                />
              )}
              {unavailable ? (
                <EmptyState
                  className="mx-auto mt-[10vh] w-full max-w-[560px]"
                  headingLevel={2}
                  title={name ? `${name} isn't answering yet` : "Not answering yet"}
                  description={
                    editor && assistant?.status !== "archived"
                      ? `${unavailable} You can test the draft in a preview while it waits.`
                      : unavailable
                  }
                  action={
                    editor && assistant?.status !== "archived" ? (
                      <Link href={previewHref(id)} className={buttonVariants()}>
                        Preview the draft
                      </Link>
                    ) : undefined
                  }
                />
              ) : (
                <EmptyState
                  className="mx-auto mt-[10vh] w-full max-w-[560px]"
                  headingLevel={2}
                  title={
                    previewing
                      ? pinned
                        ? `Preview version ${pinned}`
                        : "Preview the draft"
                      : name
                        ? `Chat with ${name}`
                        : "Start a conversation"
                  }
                  description={
                    previewing
                      ? "Try it before it goes live. Writes it asks for still wait for approval."
                      : employee
                        ? "Ask it a question. Answers show the sources they come from, so you can check them."
                        : "Ask it anything. Every answer shows the steps it took, what it cost and the sources it used."
                  }
                  action={
                    <div className="flex flex-wrap justify-center gap-2">
                      <Button onClick={() => void newChat()}>
                        <Plus aria-hidden />
                        Start a conversation
                      </Button>
                      {!previewing && <AssistantAbout assistantId={id} />}
                    </div>
                  }
                />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Says this is a preview, not Chat, and who sees it (Phase 7b.1). */
function PreviewBanner({ assistantId, version }: { assistantId: string; version?: number }) {
  return (
    <div className="border-border bg-muted text-small flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2">
      <p className="text-foreground min-w-0 flex-1">
        {version ? `Preview of version ${version}. ` : "Preview of the draft, as it is now. "}
        Only this assistant&apos;s builders and admins see previews; Chat answers from the approved
        version.
      </p>
      <Link
        href={`/assistants/${assistantId}/build`}
        className={buttonVariants({ variant: "ghost", size: "sm" })}
      >
        Back to building
      </Link>
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
