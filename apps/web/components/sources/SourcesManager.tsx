"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, dataSources, type DataSource, type DataSourceStatus } from "@/lib/api";
import { progressLabel, reportLabel } from "@/lib/ingest-status";
import { cn } from "@/lib/utils";

/** How often to re-poll while anything is still indexing. Ingestion is an Arq
 *  job; it reports each stage to Redis and the list endpoint attaches that to
 *  a processing source, so polling it shows "Embedding 3/12" as it goes. */
const POLL_MS = 2000;

const STATUS_VARIANT: Record<DataSourceStatus, "muted" | "warning" | "success" | "destructive"> = {
  pending: "muted",
  processing: "warning",
  ready: "success",
  error: "destructive",
};

function formatBytes(n: number | null): string | null {
  if (n == null) return null;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : "Something went wrong";
}

type AddMode = "file" | "url" | "text";

export function SourcesManager({ assistantId }: { assistantId: string }) {
  const [rows, setRows] = useState<DataSource[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<AddMode>("file");
  const confirm = useConfirm();
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const [urlName, setUrlName] = useState("");
  const [urlValue, setUrlValue] = useState("");
  const [textName, setTextName] = useState("");
  const [textValue, setTextValue] = useState("");

  const load = useCallback(async () => {
    try {
      setRows(await dataSources.list(assistantId));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [assistantId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll only while something is actually in flight, and stop as soon as
  // everything has settled — an idle sources tab shouldn't hit the API twice
  // a second forever.
  const indexing = (rows ?? []).some((r) => r.status === "pending" || r.status === "processing");
  useEffect(() => {
    if (!indexing) return;
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [indexing, load]);

  const run = useCallback(
    async (fn: () => Promise<unknown>) => {
      setBusy(true);
      setError(null);
      try {
        await fn();
        await load();
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const uploadFiles = useCallback(
    (files: FileList | null) => {
      if (!files?.length) return;
      void run(async () => {
        // Sequential, not Promise.all: each upload is a multi-MB body and the
        // failure of one shouldn't cancel the rest.
        for (const file of Array.from(files)) {
          await dataSources.upload(assistantId, file);
        }
      });
    },
    [assistantId, run],
  );

  const addUrl = useCallback(() => {
    const url = urlValue.trim();
    if (!url) return;
    void run(async () => {
      await dataSources.addUrl(assistantId, urlName.trim() || url, url);
      setUrlName("");
      setUrlValue("");
    });
  }, [assistantId, run, urlName, urlValue]);

  const addText = useCallback(() => {
    const text = textValue.trim();
    if (!text) return;
    void run(async () => {
      await dataSources.addText(assistantId, textName.trim() || "Pasted text", text);
      setTextName("");
      setTextValue("");
    });
  }, [assistantId, run, textName, textValue]);

  const remove = useCallback(
    (row: DataSource) => {
      // Deleting a source drops its documents and chunks too (FK cascade), so
      // the assistant genuinely forgets it — worth confirming.
      void (async () => {
        const sure = await confirm({
          title: `Delete "${row.name}"?`,
          description: "Its indexed content is removed too.",
          confirmLabel: "Delete",
          destructive: true,
        });
        if (sure) void run(() => dataSources.remove(assistantId, row.id));
      })();
    },
    [assistantId, run, confirm],
  );

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 overflow-auto p-6">
      <section className="border-border rounded-lg border">
        <div className="border-border flex items-center justify-between border-b px-4 py-3">
          <h2 className="text-sm font-semibold">Add a source</h2>
          <Tabs
            label="Kind of source"
            value={mode}
            onChange={setMode}
            options={[
              { value: "file", label: "File" },
              { value: "url", label: "URL" },
              { value: "text", label: "Text" },
            ]}
          />
        </div>

        <div className="p-4">
          {mode === "file" && (
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                uploadFiles(e.dataTransfer.files);
              }}
              className={cn(
                "flex flex-col items-center gap-2 rounded-md border border-dashed px-6 py-8 text-center transition-colors",
                dragging ? "border-primary bg-primary/5" : "border-border",
              )}
            >
              <p className="text-sm font-medium">Drop files here</p>
              <p className="text-muted-foreground text-xs">
                PDF, DOCX, Markdown, HTML or plain text · up to 50 MB each
              </p>
              <input
                ref={fileInput}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => {
                  uploadFiles(e.target.files);
                  e.target.value = "";
                }}
              />
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => fileInput.current?.click()}
              >
                Choose files
              </Button>
            </div>
          )}

          {mode === "url" && (
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="src-url">URL</Label>
                <Input
                  id="src-url"
                  placeholder="https://example.com/handbook"
                  value={urlValue}
                  onChange={(e) => setUrlValue(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="src-url-name">Name (optional)</Label>
                <Input
                  id="src-url-name"
                  placeholder="Defaults to the URL"
                  value={urlName}
                  onChange={(e) => setUrlName(e.target.value)}
                />
              </div>
              <div>
                <Button size="sm" onClick={addUrl} disabled={busy || !urlValue.trim()}>
                  Add URL
                </Button>
              </div>
            </div>
          )}

          {mode === "text" && (
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="src-text-name">Name</Label>
                <Input
                  id="src-text-name"
                  placeholder="Pasted text"
                  value={textName}
                  onChange={(e) => setTextName(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="src-text">Content</Label>
                <Textarea
                  id="src-text"
                  rows={6}
                  placeholder="Paste the content to index…"
                  value={textValue}
                  onChange={(e) => setTextValue(e.target.value)}
                />
              </div>
              <div>
                <Button size="sm" onClick={addText} disabled={busy || !textValue.trim()}>
                  Add text
                </Button>
              </div>
            </div>
          )}
        </div>
      </section>

      {error && (
        <p className="text-destructive text-sm" role="alert">
          {error}
        </p>
      )}

      <section className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Sources{rows ? ` (${rows.length})` : ""}</h2>
          {indexing && <span className="text-muted-foreground text-xs">indexing…</span>}
        </div>

        {rows === null && <p className="text-muted-foreground text-sm">Loading…</p>}
        {rows?.length === 0 && (
          <p className="text-muted-foreground border-border rounded-md border border-dashed px-4 py-8 text-center text-sm">
            No sources yet. Add one above and it will be indexed automatically.
          </p>
        )}

        <ul className="flex flex-col gap-2">
          {(rows ?? []).map((row) => (
            <SourceRow
              key={row.id}
              row={row}
              busy={busy}
              onReindex={() => void run(() => dataSources.reindex(assistantId, row.id))}
              onDelete={() => remove(row)}
            />
          ))}
        </ul>
      </section>
    </div>
  );
}

function SourceRow({
  row,
  busy,
  onReindex,
  onDelete,
}: {
  row: DataSource;
  busy: boolean;
  onReindex: () => void;
  onDelete: () => void;
}) {
  const size = formatBytes(row.bytes);
  return (
    <li className="border-border rounded-md border px-4 py-3">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{row.name}</span>
            <Badge variant={STATUS_VARIANT[row.status]}>{row.status}</Badge>
          </div>
          <p className="text-muted-foreground mt-1 truncate text-xs">
            {row.type}
            {size && ` · ${size}`}
            {/* Counts are the honest signal that indexing produced anything.
                A "ready" source with 0 chunks is searchable in name only. */}
            {row.status === "ready" &&
              ` · ${row.document_count} doc${row.document_count === 1 ? "" : "s"} · ${row.chunk_count} chunk${row.chunk_count === 1 ? "" : "s"}`}
            {row.uri && ` · ${row.uri}`}
          </p>
          {row.error && <p className="text-destructive mt-1 text-xs">{row.error}</p>}
          {progressLabel(row) && (
            <p className="text-muted-foreground mt-1 text-xs" aria-live="polite">
              {progressLabel(row)}
            </p>
          )}
          {reportLabel(row) && (
            <p className="text-muted-foreground mt-1 text-xs">Last index: {reportLabel(row)}</p>
          )}
          {row.status === "ready" && row.ingest_report?.context_skipped && (
            <p className="text-warning mt-1 text-xs">
              Indexed without context lines: {row.ingest_report.context_skipped}.
            </p>
          )}
          {row.status === "ready" && row.chunk_count === 0 && (
            <p className="text-warning mt-1 text-xs">
              Indexed but produced no chunks — nothing here is searchable.
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button size="sm" variant="outline" onClick={onReindex} disabled={busy}>
            Reindex
          </Button>
          <Button size="sm" variant="ghost" onClick={onDelete} disabled={busy}>
            Delete
          </Button>
        </div>
      </div>
    </li>
  );
}
