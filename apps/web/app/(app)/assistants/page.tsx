"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { assistants, type Assistant } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function AssistantsPage() {
  const { activeOrgId } = useAuth();
  const router = useRouter();
  const [rows, setRows] = useState<Assistant[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await assistants.list());
    } finally {
      setLoading(false);
    }
  }, []);

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
        {rows.map((a) => (
          <button
            key={a.id}
            onClick={() => router.push(`/assistants/${a.id}/build`)}
            className="border-border bg-card hover:border-primary/40 flex items-center justify-between rounded-lg border px-4 py-3 text-left shadow-[0_1px_3px_var(--shadow-color)] transition-all hover:-translate-y-0.5 hover:shadow-md"
          >
            <div>
              <div className="text-sm font-medium">{a.name}</div>
              <div className="text-muted-foreground text-xs">{a.slug}</div>
            </div>
            <Badge variant={a.status === "published" ? "success" : "muted"}>{a.status}</Badge>
          </button>
        ))}
      </div>
    </div>
  );
}
