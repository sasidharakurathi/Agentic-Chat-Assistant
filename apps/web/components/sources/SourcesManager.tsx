"use client";

import { Upload } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Loading } from "@/components/ui/loading";
import { SectionHeading } from "@/components/ui/section-heading";
import { Segmented } from "@/components/ui/segmented";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, dataSources, type DataSource } from "@/lib/api";
import { formatRelative, failureMessage } from "@/lib/format";
import { progressLabel, reportLabel } from "@/lib/ingest-status";
import { cn } from "@/lib/utils";

/** How often to re-poll while anything is still indexing. Ingestion is an Arq
 *  job; it reports each stage to Redis and the list endpoint attaches that to
 *  a processing source, so polling it shows "Embedding passages: 3 of 12" as
 *  it goes. */
const POLL_MS = 2000;

const TYPE_LABEL: Record<DataSource["type"], string> = {
  file: "File",
  url: "Web page",
  text: "Pasted text",
};

function formatBytes(n: number | null): string | null {
  if (n == null) return null;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The server's own message, or null when it could not be reached. */
function reasonOf(err: unknown): string | null {
  return err instanceof ApiError ? err.message : null;
}

type Failure = { what: string; todo: string };
type AddMode = "file" | "url" | "text";

export function SourcesManager({ assistantId }: { assistantId: string }) {
  const [rows, setRows] = useState<DataSource[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(0);
  const [mode, setMode] = useState<AddMode>("file");
  const confirm = useConfirm();
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const ids = useId();

  const [urlName, setUrlName] = useState("");
  const [urlValue, setUrlValue] = useState("");
  const [textName, setTextName] = useState("");
  const [textValue, setTextValue] = useState("");

  const load = useCallback(async () => {
    try {
      setRows(await dataSources.list(assistantId));
    } catch (err) {
      setError(
        failureMessage(
          "Couldn't load the sources.",
          reasonOf(err),
          "Reload the page to try again.",
        ),
      );
    }
  }, [assistantId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll only while something is actually in flight, and stop as soon as
  // everything has settled: an idle sources tab shouldn't hit the API twice
  // a second forever.
  const indexing = (rows ?? []).some((r) => r.status === "pending" || r.status === "processing");
  useEffect(() => {
    if (!indexing) return;
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [indexing, load]);

  const run = useCallback(
    async (fn: () => Promise<unknown>, failure: Failure) => {
      setBusy(true);
      setError(null);
      try {
        await fn();
        await load();
      } catch (err) {
        setError(failureMessage(failure.what, reasonOf(err), failure.todo));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const uploadFiles = useCallback(
    (files: FileList | null) => {
      if (!files?.length) return;
      const list = Array.from(files);
      let current = "";
      setUploading(list.length);
      void run(
        async () => {
          // Sequential, not Promise.all: each upload is a multi-MB body and the
          // failure of one shouldn't cancel the rest.
          for (const file of list) {
            current = file.name;
            await dataSources.upload(assistantId, file);
          }
        },
        {
          get what() {
            return `Couldn't upload ${current || "the file"}.`;
          },
          todo: "Check that it is a supported file under 50 MB, then upload it again.",
        },
      ).finally(() => setUploading(0));
    },
    [assistantId, run],
  );

  const addUrl = useCallback(() => {
    const url = urlValue.trim();
    if (!url) return;
    void run(
      async () => {
        await dataSources.addUrl(assistantId, urlName.trim() || url, url);
        setUrlName("");
        setUrlValue("");
      },
      {
        what: "Couldn't add the web page.",
        todo: "Check the URL, then add it again.",
      },
    );
  }, [assistantId, run, urlName, urlValue]);

  const addText = useCallback(() => {
    const text = textValue.trim();
    if (!text) return;
    void run(
      async () => {
        await dataSources.addText(assistantId, textName.trim() || "Pasted text", text);
        setTextName("");
        setTextValue("");
      },
      { what: "Couldn't add the text.", todo: "Try adding it again." },
    );
  }, [assistantId, run, textName, textValue]);

  const remove = useCallback(
    (row: DataSource) => {
      // Deleting a source drops its documents and chunks too (FK cascade), so
      // the assistant genuinely forgets it: worth confirming.
      void (async () => {
        const sure = await confirm({
          title: `Delete "${row.name}"?`,
          description:
            "Its indexed content is deleted too, so the assistant can no longer find anything from it.",
          confirmLabel: "Delete source",
          destructive: true,
        });
        if (sure)
          void run(() => dataSources.remove(assistantId, row.id), {
            what: `Couldn't delete ${row.name}.`,
            todo: "Try again.",
          });
      })();
    },
    [assistantId, run, confirm],
  );

  return (
    <div className="min-h-0 flex-1 overflow-auto px-4 py-6 md:px-6 lg:px-8">
      <div className="mx-auto flex w-full max-w-[40rem] flex-col gap-6">
        <SectionHeading
          level={2}
          title="Sources"
          description="Documents the assistant can search: files, web pages and pasted text. Each one is indexed automatically after you add it."
        />

        <section
          aria-labelledby={`${ids}-add`}
          className="border-border bg-card flex flex-col gap-4 rounded-lg border p-4 sm:p-6"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 id={`${ids}-add`} className="text-h3 font-semibold">
              Add a source
            </h3>
            <Segmented
              label="Kind of source"
              value={mode}
              onChange={setMode}
              options={[
                { value: "file", label: "File" },
                { value: "url", label: "Web page" },
                { value: "text", label: "Text" },
              ]}
            />
          </div>

          {mode === "file" && (
            <div
              role="group"
              aria-label="Upload files"
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
                "flex flex-col items-center gap-2 rounded-md border px-6 py-8 text-center transition-colors duration-120 ease-out",
                dragging ? "border-ring bg-muted ring-ring ring-1" : "border-field-border",
              )}
            >
              <Upload aria-hidden className="text-muted-foreground size-5" />
              <p className="font-medium">Drop files here</p>
              <p className="text-small text-muted-foreground">
                PDF, Word (DOCX), Markdown, HTML or plain text, up to 50 MB each.
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
              <p className="text-small text-muted-foreground min-h-[17px]" aria-live="polite">
                {uploading > 0 ? `Uploading ${plural(uploading, "file", "files")}…` : ""}
              </p>
            </div>
          )}

          {mode === "url" && (
            <form
              className="flex flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                addUrl();
              }}
            >
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="src-url">URL</Label>
                <Input
                  id="src-url"
                  type="url"
                  inputMode="url"
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
                <Button type="submit" disabled={busy || !urlValue.trim()}>
                  Add web page
                </Button>
              </div>
            </form>
          )}

          {mode === "text" && (
            <form
              className="flex flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                addText();
              }}
            >
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
                  placeholder="Paste the content to index"
                  value={textValue}
                  onChange={(e) => setTextValue(e.target.value)}
                />
              </div>
              <div>
                <Button type="submit" disabled={busy || !textValue.trim()}>
                  Add text
                </Button>
              </div>
            </form>
          )}
        </section>

        {error && <Alert>{error}</Alert>}

        <section aria-labelledby={`${ids}-list`} className="flex flex-col gap-3">
          <h3 id={`${ids}-list`} className="text-h3 font-semibold">
            Added sources
          </h3>

          {rows === null && !error && <Loading what="sources" rows={3} rowHeight={72} />}
          {rows?.length === 0 && (
            <EmptyState
              title="Add your first source"
              description="Upload a file, add a web page or paste text above. Once it is indexed, the knowledge base on the canvas can search it."
            />
          )}

          {rows && rows.length > 0 && (
            <ul className="border-border flex flex-col border-t">
              {rows.map((row) => (
                <SourceRow
                  key={row.id}
                  row={row}
                  busy={busy}
                  onReindex={() =>
                    void run(() => dataSources.reindex(assistantId, row.id), {
                      what: `Couldn't reindex ${row.name}.`,
                      todo: "Try again in a moment.",
                    })
                  }
                  onDelete={() => remove(row)}
                />
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

function SourceStatus({ row }: { row: DataSource }) {
  switch (row.status) {
    case "pending":
      return (
        <Badge variant="muted" live>
          Queued
        </Badge>
      );
    case "processing":
      return (
        <Badge variant="muted" live>
          Indexing
        </Badge>
      );
    case "ready":
      // A "ready" source with no passages is searchable in name only.
      return row.chunk_count === 0 ? (
        <Badge variant="warning">Nothing to search</Badge>
      ) : (
        <Badge variant="success">Ready</Badge>
      );
    case "error":
      return <Badge variant="destructive">Failed</Badge>;
  }
}

/** "File, 1.2 MB, 3 documents" in plain words; for a web page, its address
 *  follows on its own line. */
function describeSource(row: DataSource): string {
  const parts = [TYPE_LABEL[row.type]];
  const size = formatBytes(row.bytes);
  if (size) parts.push(size);
  if (row.status === "ready" && row.document_count > 1) {
    parts.push(plural(row.document_count, "document", "documents"));
  }
  return parts.join(", ");
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
  const progress = progressLabel(row);
  const report = reportLabel(row);
  return (
    <li className="border-border flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b py-4">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 truncate font-medium">{row.name}</span>
          <SourceStatus row={row} />
        </div>
        <p className="text-small text-muted-foreground num">
          {describeSource(row)}
          {row.status === "ready" && row.indexed_at && (
            <>. Indexed {formatRelative(row.indexed_at)}</>
          )}
          .
        </p>
        {row.type === "url" && row.uri && row.uri !== row.name && (
          <p className="text-small text-muted-foreground break-all">{row.uri}</p>
        )}
        {row.error && (
          <p className="text-small text-destructive break-words">
            Indexing failed: {row.error.trim().replace(/[.!?]+$/, "")}. Fix the source, then reindex
            it.
          </p>
        )}
        <p className="text-small text-muted-foreground num empty:hidden" aria-live="polite">
          {progress}
        </p>
        {report && <p className="text-small text-muted-foreground num">Last index: {report}</p>}
        {row.status === "ready" && row.ingest_report?.context_skipped && (
          <p className="text-small text-warning">
            Indexed without context lines: {row.ingest_report.context_skipped}.
          </p>
        )}
        {row.status === "ready" && row.chunk_count === 0 && (
          <p className="text-small text-warning">
            Indexing found no text, so nothing here can be searched. Check the file has selectable
            text, then reindex it.
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1">
        <Button size="sm" variant="ghost" onClick={onReindex} disabled={busy}>
          Reindex
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={onDelete}
          disabled={busy}
          className="hover:text-destructive focus-visible:text-destructive"
        >
          Delete
        </Button>
      </div>
    </li>
  );
}
