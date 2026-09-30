"use client";

import { Archive, Pencil, Plus } from "lucide-react";
import { useId, type ReactNode } from "react";

import { groupByDay } from "@/components/chat/conversation-groups";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { BackLink } from "@/components/ui/page-header";
import type { Conversation } from "@/lib/api";
import { cn } from "@/lib/utils";

/** The chat page's conversation list (docs/DESIGN.md section 7, Chat): a
 *  back link to the assistant's settings, New chat, then the conversations
 *  grouped Today, Yesterday and Earlier. The open one has the muted fill,
 *  the marker bar and `aria-current`. Rename and Archive show on hover, on
 *  focus and always on the open row (and always on touch screens). Used in
 *  the side column and, below 768px, in a sheet. */
export function ConversationList({
  assistantId,
  name,
  rows,
  active,
  more,
  loadingMore = false,
  error,
  onSelect,
  onNewChat,
  onRename,
  onArchive,
  onLoadMore,
  headerAction,
}: {
  assistantId: string;
  name: string;
  rows: Conversation[];
  active: string | null;
  more: boolean;
  loadingMore?: boolean;
  error?: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onRename: (c: Conversation) => void;
  onArchive: (c: Conversation) => void;
  onLoadMore: () => void;
  /** Extra control at the end of the top row, e.g. the sheet's close button. */
  headerAction?: ReactNode;
}) {
  const baseId = useId();
  const groups = groupByDay(rows);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-border flex min-h-14 shrink-0 items-center justify-between gap-2 border-b px-4 py-2">
        <BackLink
          href={`/assistants/${assistantId}/build`}
          label={name ? `${name} settings` : "Assistant settings"}
          className="max-w-full min-w-0 break-words"
        />
        {headerAction}
      </div>
      <div className="shrink-0 px-3 pt-3">
        <Button variant="outline" size="sm" className="w-full" onClick={onNewChat}>
          <Plus aria-hidden />
          New chat
        </Button>
      </div>
      <div className="relative min-h-0 flex-1 overflow-y-auto px-2 pt-1 pb-3">
        {error && (
          <Alert className="text-small mx-1 mt-2">
            <span>{error}</span>
          </Alert>
        )}
        {groups.map((g) => {
          const headingId = `${baseId}-${g.label}`;
          return (
            <section key={g.label} aria-labelledby={headingId}>
              <h2
                id={headingId}
                className="text-small text-muted-foreground px-3 pt-4 pb-1 font-medium"
              >
                {g.label}
              </h2>
              <ul className="flex flex-col gap-0.5">
                {g.items.map((c) => (
                  <Row
                    key={c.id}
                    c={c}
                    active={c.id === active}
                    onSelect={onSelect}
                    onRename={onRename}
                    onArchive={onArchive}
                  />
                ))}
              </ul>
            </section>
          );
        })}
        {rows.length === 0 && (
          <p className="text-small text-muted-foreground px-3 py-4">No conversations yet.</p>
        )}
        {more && (
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-foreground mt-3 w-full"
            disabled={loadingMore}
            onClick={onLoadMore}
          >
            {loadingMore ? "Loading older conversations…" : "Load older conversations"}
          </Button>
        )}
      </div>
    </div>
  );
}

function Row({
  c,
  active,
  onSelect,
  onRename,
  onArchive,
}: {
  c: Conversation;
  active: boolean;
  onSelect: (id: string) => void;
  onRename: (c: Conversation) => void;
  onArchive: (c: Conversation) => void;
}) {
  return (
    <li className="group relative flex items-center">
      {active && (
        <span
          aria-hidden
          className="bg-marker absolute inset-y-1.5 left-0 z-10 w-[3px] rounded-full"
        />
      )}
      <button
        type="button"
        onClick={() => onSelect(c.id)}
        aria-current={active ? "true" : undefined}
        title={c.title}
        className={cn(
          "focus-visible:ring-ring h-9 min-w-0 flex-1 truncate rounded-md pr-3 pl-3 text-left transition-colors duration-120 ease-out group-focus-within:pr-[68px] group-hover:pr-[68px] focus-visible:ring-2 focus-visible:outline-none pointer-coarse:pr-[68px]",
          active
            ? "bg-muted text-foreground pr-[68px] font-semibold"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
      >
        {c.title}
      </button>
      <div
        className={cn(
          "absolute right-1 flex items-center gap-0.5 transition-opacity duration-120 ease-out",
          active
            ? "opacity-100"
            : "opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 pointer-coarse:opacity-100",
        )}
      >
        <Button
          variant="ghost"
          size="icon"
          className="hover:bg-background size-7"
          aria-label={`Rename ${c.title}`}
          title="Rename"
          onClick={() => onRename(c)}
        >
          <Pencil aria-hidden className="size-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="hover:bg-background hover:text-destructive size-7"
          aria-label={`Archive ${c.title}`}
          title="Archive"
          onClick={() => onArchive(c)}
        >
          <Archive aria-hidden className="size-3.5" />
        </Button>
      </div>
    </li>
  );
}
