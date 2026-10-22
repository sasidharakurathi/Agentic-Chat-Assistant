"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Loading } from "@/components/ui/loading";
import { PageHeader } from "@/components/ui/page-header";
import { SectionHeading } from "@/components/ui/section-heading";
import {
  ApiError,
  chat,
  conversations,
  type ChatAssistant,
  type ChatConversation,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { failureMessage, formatRelative } from "@/lib/format";
import { categoriesOf } from "@/lib/listing";
import { isEmployee } from "@/lib/roles";
import { useApprovalSummary } from "@/lib/use-approval-summary";

/** Chat (Phase 8.1): the assistants that answer, and your conversations.
 *  Where an employee lands; the catalog proper comes with audiences (8.5)
 *  and the Chat app (8.6). */
export default function ChatHomePage() {
  const { activeOrgId, activeRole } = useAuth();
  const [assistants, setAssistants] = useState<ChatAssistant[] | null>(null);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [mine, setMine] = useState<ChatConversation[]>([]);
  const [more, setMore] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Phase 8.8: searching your conversations, and the ones you removed. */
  const [find, setFind] = useState("");
  const [removed, setRemoved] = useState(false);
  // Requests still waiting or running (Phase 9.2).
  const summary = useApprovalSummary(true);

  const fail = (what: string, err: unknown) =>
    setError(
      failureMessage(
        what,
        err instanceof ApiError ? err.message : null,
        "Check your connection, then reload the page.",
      ),
    );

  const load = useCallback(async () => {
    if (!activeOrgId) return;
    setError(null);
    try {
      setAssistants(await chat.assistants());
    } catch (err) {
      fail("Couldn't load Chat.", err);
    }
  }, [activeOrgId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Your conversations: as you type, a moment after the last key.
  useEffect(() => {
    if (!activeOrgId) return;
    const t = setTimeout(() => {
      chat
        .conversations(null, { q: find, removed })
        .then((page) => {
          setMine(page.items);
          setMore(page.next_cursor);
        })
        .catch((err) => fail("Couldn't load your conversations.", err));
    }, 250);
    return () => clearTimeout(t);
  }, [activeOrgId, find, removed]);

  const restore = async (c: ChatConversation) => {
    try {
      await conversations.restore(c.id);
      setMine((m) => m.filter((x) => x.id !== c.id));
    } catch (err) {
      fail("Couldn't put the conversation back.", err);
    }
  };

  const loadMore = async () => {
    if (!more) return;
    setLoadingMore(true);
    try {
      const page = await chat.conversations(more, { q: find, removed });
      setMine((m) => [...m, ...page.items]);
      setMore(page.next_cursor);
    } catch (err) {
      fail("Couldn't load more conversations.", err);
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-240 px-4 py-8 md:px-6 lg:px-8">
      <PageHeader
        title="Chat"
        description={
          isEmployee(activeRole)
            ? "Ask an assistant. Your conversations are yours: an admin can open one only by giving a reason, and that is recorded."
            : "The assistants that answer in Chat, as everyone in your organisation sees them."
        }
      />
      <p className="text-small text-muted-foreground mb-6">
        <Link href="/chat/requests" className="text-foreground underline underline-offset-4">
          My requests
        </Link>
        {summary?.mine_open ? `: ${summary.mine_open} still open` : ""}
      </p>
      {error && <Alert className="mb-6">{error}</Alert>}

      {assistants === null && !error ? (
        <Loading what="assistants" rows={3} />
      ) : (
        assistants && (
          <div className="flex flex-col gap-12">
            <section aria-labelledby="chat-assistants" className="flex flex-col gap-4">
              <SectionHeading id="chat-assistants" title="Assistants" />
              {assistants.length > 0 && (
                <div className="flex flex-col gap-3">
                  <Input
                    type="search"
                    aria-label="Search assistants"
                    placeholder="Search assistants"
                    className="max-w-80"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                  {categoriesOf(assistants).length > 0 && (
                    <div role="group" aria-label="Category" className="flex flex-wrap gap-2">
                      {["", ...categoriesOf(assistants)].map((c) => (
                        <Button
                          key={c || "all"}
                          size="sm"
                          variant={category === c ? "default" : "outline"}
                          aria-pressed={category === c}
                          onClick={() => setCategory(c)}
                        >
                          {c || "All"}
                        </Button>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {assistants.length === 0 ? (
                <EmptyState
                  title="No assistant answers in Chat yet"
                  description="An assistant appears here once an admin approves it."
                />
              ) : (
                <ul className="grid gap-3 sm:grid-cols-2">
                  {shown(assistants, query, category).map((a) => (
                    <li key={a.id}>
                      <Link
                        href={`/assistants/${a.id}/chat`}
                        className="border-border hover:bg-muted focus-visible:ring-ring flex h-full flex-col gap-1 rounded-md border p-4 focus-visible:ring-2 focus-visible:outline-none"
                      >
                        <span className="flex items-center gap-2">
                          <span className="font-medium">{a.name}</span>
                          {a.official && <Badge variant="success">Official</Badge>}
                        </span>
                        {a.description && (
                          <span className="text-small text-muted-foreground line-clamp-2">
                            {a.description}
                          </span>
                        )}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section aria-labelledby="chat-mine" className="flex flex-col gap-3">
              <SectionHeading id="chat-mine" title="Your conversations" />
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  type="search"
                  aria-label="Search your conversations"
                  placeholder="Search your conversations"
                  className="max-w-80"
                  value={find}
                  onChange={(e) => setFind(e.target.value)}
                />
                <div role="group" aria-label="Which conversations" className="flex gap-2">
                  <Button
                    size="sm"
                    variant={removed ? "outline" : "default"}
                    aria-pressed={!removed}
                    onClick={() => setRemoved(false)}
                  >
                    In your history
                  </Button>
                  <Button
                    size="sm"
                    variant={removed ? "default" : "outline"}
                    aria-pressed={removed}
                    onClick={() => setRemoved(true)}
                  >
                    Removed
                  </Button>
                </div>
              </div>
              {removed && (
                <p className="text-small text-muted-foreground">
                  Removed from your history: your company keeps them, and admins can still open
                  them. Put one back to see it in your lists again.
                </p>
              )}
              {mine.length === 0 ? (
                <p className="text-muted-foreground border-border border-t pt-3">
                  {find.trim()
                    ? "Nothing matches that."
                    : removed
                      ? "You haven't removed any."
                      : "None yet. Pick an assistant above to start one."}
                </p>
              ) : (
                <ul className="border-border border-t">
                  {mine.map((c) => (
                    <li key={c.id} className="border-border flex items-center gap-2 border-b">
                      <Link
                        href={`/assistants/${c.assistant_id}/chat?conversation=${c.id}`}
                        className="hover:bg-muted focus-visible:ring-ring flex min-w-0 flex-1 flex-wrap items-baseline gap-x-4 gap-y-0.5 px-1 py-2.5 focus-visible:ring-2 focus-visible:outline-none"
                      >
                        <span className="min-w-0 flex-[1_1_16rem] truncate">{c.title}</span>
                        <span className="text-small text-muted-foreground">
                          {c.assistant_name}, {formatRelative(c.last_message_at ?? c.created_at)}
                        </span>
                      </Link>
                      {removed && (
                        <Button size="sm" variant="ghost" onClick={() => void restore(c)}>
                          Put back
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {more && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="self-start"
                  disabled={loadingMore}
                  onClick={() => void loadMore()}
                >
                  {loadingMore ? "Loading…" : "Load more"}
                </Button>
              )}
            </section>
          </div>
        )
      )}
    </div>
  );
}

/** The list as filtered here: the server already chose what this person
 *  may see, Official first. */
function shown(list: ChatAssistant[], query: string, category: string): ChatAssistant[] {
  const q = query.trim().toLowerCase();
  return list.filter(
    (a) =>
      (!category || a.category.toLowerCase() === category.toLowerCase()) &&
      (!q || a.name.toLowerCase().includes(q) || a.description.toLowerCase().includes(q)),
  );
}
