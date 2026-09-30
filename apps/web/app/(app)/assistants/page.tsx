"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Loading } from "@/components/ui/loading";
import { PageHeader } from "@/components/ui/page-header";
import { useToast } from "@/components/ui/toast";
import { ApiError, assistants, type Assistant } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { failureMessage, formatDate, formatRelative } from "@/lib/format";

const STATUS: Record<Assistant["status"], { label: string; variant: "success" | "muted" }> = {
  published: { label: "Published", variant: "success" },
  draft: { label: "Draft", variant: "muted" },
  archived: { label: "Archived", variant: "muted" },
};

function reason(err: unknown): string | null {
  return err instanceof ApiError ? err.message : null;
}

export default function AssistantsPage() {
  const { activeOrgId } = useAuth();
  const router = useRouter();
  const [rows, setRows] = useState<Assistant[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Cursor for the next page; null once everything is loaded.
  const [more, setMore] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const confirm = useConfirm();
  const toast = useToast();

  async function remove(a: Assistant) {
    const sure = await confirm({
      title: `Delete "${a.name}"?`,
      description:
        "Its conversations, documents and database connections (including stored " +
        "credentials) are deleted permanently. Usage history is kept.",
      // Name the thing being deleted (spec 9: "Delete Shop Helper"); a long
      // name falls back to the noun so the button fits a phone-width dialog.
      confirmLabel: a.name.length <= 24 ? `Delete ${a.name}` : "Delete assistant",
      destructive: true,
    });
    if (!sure) return;
    setDeleting(a.id);
    setError(null);
    try {
      await assistants.remove(a.id);
      setRows((prev) => prev.filter((x) => x.id !== a.id));
      toast(`Deleted "${a.name}"`, "success");
    } catch (err) {
      setError(failureMessage(`Couldn't delete ${a.name}.`, reason(err), "Try again."));
    } finally {
      setDeleting(null);
    }
  }

  const load = useCallback(async () => {
    setLoading(true);
    setLoadFailed(false);
    setError(null);
    try {
      const page = await assistants.list();
      setRows(page.items);
      setMore(page.next_cursor);
    } catch (err) {
      setLoadFailed(true);
      setError(
        failureMessage(
          "Couldn't load your assistants.",
          reason(err),
          "Check your connection, then reload the page.",
        ),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  async function loadMore() {
    if (!more) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await assistants.list(more);
      // Keyed by id: the list is ordered by last edit, so an assistant edited
      // since page 1 was fetched could otherwise appear twice.
      setRows((prev) => {
        const seen = new Set(prev.map((a) => a.id));
        return [...prev, ...page.items.filter((a) => !seen.has(a.id))];
      });
      setMore(page.next_cursor);
    } catch (err) {
      setError(failureMessage("Couldn't load more assistants.", reason(err), "Try again."));
    } finally {
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    if (activeOrgId) void load();
  }, [activeOrgId, load]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const a = await assistants.create(name.trim());
      router.push(`/assistants/${a.id}/build`);
    } catch (err) {
      setError(
        failureMessage(
          "Couldn't create the assistant.",
          reason(err),
          "Try another name, or try again.",
        ),
      );
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-240 px-4 py-8 md:px-6 lg:px-8">
      <PageHeader
        title="Assistants"
        description="Build an assistant by connecting what it can use: documents, databases, tools."
        actions={
          <>
            <Link href="/assistants/new" className={buttonVariants({ variant: "outline" })}>
              Guided setup
            </Link>
            <form onSubmit={create} className="flex w-full min-w-0 gap-2 sm:w-auto">
              <Label htmlFor="quick-create-name" className="sr-only">
                New assistant name
              </Label>
              <Input
                id="quick-create-name"
                placeholder="New assistant name"
                autoComplete="off"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="min-w-0 sm:w-56"
              />
              <Button type="submit" disabled={creating}>
                {creating ? "Creating…" : "Create"}
              </Button>
            </form>
          </>
        }
      />

      <div className="flex flex-col gap-4">
        {error && <Alert>{error}</Alert>}

        {loading && <Loading what="assistants" rows={3} rowHeight={65} />}

        {!loading && !loadFailed && rows.length === 0 && (
          <EmptyState
            title="Build your first assistant"
            description="Answer a few questions and start with a pipeline already wired up, or type a name above and press Create to start from an empty canvas."
            action={
              <Link href="/assistants/new" className={buttonVariants({ variant: "default" })}>
                Start guided setup
              </Link>
            }
          />
        )}

        {!loading && rows.length > 0 && (
          <ul aria-label="Assistants" className="border-border border-t">
            {rows.map((a) => {
              const status = STATUS[a.status] ?? { label: a.status, variant: "muted" as const };
              return (
                // Two sibling actions rather than a button inside a button,
                // which is invalid HTML and swallows the inner click.
                <li
                  key={a.id}
                  className="border-border flex flex-wrap items-center gap-x-4 gap-y-2 border-b py-3"
                >
                  <Link
                    href={`/assistants/${a.id}/build`}
                    className="group focus-visible:ring-ring min-w-0 flex-[1_1_14rem] rounded-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
                  >
                    <span className="text-reading block truncate font-medium decoration-1 underline-offset-[3px] group-hover:underline">
                      {a.name}
                    </span>
                    <span className="text-small text-muted-foreground block truncate">
                      {a.slug}
                    </span>
                  </Link>
                  <div className="flex items-center gap-4">
                    <Badge variant={status.variant}>{status.label}</Badge>
                    <span className="text-small text-muted-foreground hidden w-32 md:inline">
                      Edited{" "}
                      <time dateTime={a.updated_at} title={formatDate(a.updated_at)}>
                        {formatRelative(a.updated_at)}
                      </time>
                    </span>
                  </div>
                  <div className="-mr-3 ml-auto flex items-center gap-1">
                    <Link
                      href={`/assistants/${a.id}/chat`}
                      aria-label={`Chat with ${a.name}`}
                      className={buttonVariants({ variant: "ghost", size: "sm" })}
                    >
                      Chat
                    </Link>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Delete ${a.name}`}
                      disabled={deleting === a.id}
                      onClick={() => void remove(a)}
                      className="hover:text-destructive focus-visible:text-destructive"
                    >
                      {deleting === a.id ? "Deleting…" : "Delete"}
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {more && !loading && (
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
      </div>
    </div>
  );
}
