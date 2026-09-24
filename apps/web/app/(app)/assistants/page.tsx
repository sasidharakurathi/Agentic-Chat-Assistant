"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { Input } from "@/components/ui/input";
import { ApiError, assistants, type Assistant } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function AssistantsPage() {
  const { activeOrgId } = useAuth();
  const router = useRouter();
  const [rows, setRows] = useState<Assistant[]>([]);
  const [loading, setLoading] = useState(true);
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
      confirmLabel: "Delete",
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
      setError(err instanceof ApiError ? err.message : "Could not delete the assistant");
    } finally {
      setDeleting(null);
    }
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await assistants.list();
      setRows(page.items);
      setMore(page.next_cursor);
    } finally {
      setLoading(false);
    }
  }, []);

  async function loadMore() {
    if (!more) return;
    setLoadingMore(true);
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
      setError(err instanceof ApiError ? err.message : "Could not load more assistants");
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
    try {
      const a = await assistants.create(name.trim());
      router.push(`/assistants/${a.id}/build`);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="font-serif text-2xl font-semibold tracking-tight">Assistants</h1>
      <p className="text-muted-foreground mt-1 text-sm">
        Each assistant is a configurable agentic pipeline.
      </p>

      <div className="mt-6 flex items-start justify-between gap-4">
        <form onSubmit={create} className="flex flex-1 gap-2">
          <Input
            placeholder="New assistant name…"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button type="submit" disabled={creating}>
            {creating ? "Creating…" : "Create"}
          </Button>
        </form>
        <Link href="/assistants/new" className={buttonVariants({ variant: "outline" })}>
          Guided setup
        </Link>
      </div>

      <div className="mt-6 flex flex-col gap-2">
        {loading && <p className="text-muted-foreground text-sm">Loading…</p>}
        {!loading && rows.length === 0 && (
          <p className="text-muted-foreground rounded-lg border border-dashed p-8 text-center text-sm">
            No assistants yet. Create one above.
          </p>
        )}
        {error && (
          <p className="text-destructive text-sm" role="alert">
            {error}
          </p>
        )}
        {rows.map((a) => (
          // Two sibling actions rather than a button inside a button, which
          // is invalid HTML and swallows the inner click.
          <div
            key={a.id}
            className="border-border bg-card hover:border-primary/40 flex items-center gap-3 rounded-lg border pr-3 shadow-[0_1px_3px_var(--shadow-color)] transition-all hover:-translate-y-0.5 hover:shadow-md"
          >
            <button
              onClick={() => router.push(`/assistants/${a.id}/build`)}
              className="flex flex-1 items-center justify-between px-4 py-3 text-left"
            >
              <div>
                <div className="text-sm font-medium">{a.name}</div>
                <div className="text-muted-foreground text-xs">{a.slug}</div>
              </div>
              <Badge variant={a.status === "published" ? "success" : "muted"}>{a.status}</Badge>
            </button>
            <Button
              size="sm"
              variant="ghost"
              aria-label={`Delete ${a.name}`}
              disabled={deleting === a.id}
              onClick={() => void remove(a)}
            >
              {deleting === a.id ? "Deleting…" : "Delete"}
            </Button>
          </div>
        ))}
        {more && (
          <Button
            variant="ghost"
            size="sm"
            className="self-center"
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
