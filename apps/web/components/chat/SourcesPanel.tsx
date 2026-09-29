"use client";

import { useCallback, useState } from "react";

import { dataSources, type Citation } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Human-readable location line: page range, heading trail, char range.
 *  Each part is omitted when the format genuinely doesn't have it — a DOCX
 *  has headings but no pages, a PDF the reverse, pasted text neither — rather
 *  than printing a placeholder that implies more precision than exists. */
function locLine(c: Citation): string {
  const parts: string[] = [];
  const { page, page_end, char_start, char_end, breadcrumb } = c.loc ?? {};
  if (page != null) {
    parts.push(page_end != null && page_end > page ? `pp. ${page}–${page_end}` : `p. ${page}`);
  }
  if (breadcrumb?.length) parts.push(breadcrumb.join(" › "));
  if (char_start != null && char_end != null) parts.push(`chars ${char_start}–${char_end}`);
  return parts.join(" · ");
}

function kindLabel(c: Citation): string {
  switch (c.source_type) {
    case "file":
      return "file";
    case "url":
      return "web";
    case "text":
      return "text";
    default:
      return "source";
  }
}

export function SourcesPanel({
  citations,
  assistantId,
  activeMarker,
  onFocus,
}: {
  citations: Citation[];
  assistantId: string;
  activeMarker?: number | null;
  onFocus?: (marker: number | null) => void;
}) {
  if (!citations.length) return null;
  return (
    <div className="border-border mt-2 rounded-md border">
      <div className="text-muted-foreground border-border border-b px-3 py-1.5 text-xs font-medium">
        Sources
      </div>
      <ul className="divide-border divide-y">
        {citations.map((c) => (
          <SourceRow
            key={`${c.marker}-${c.chunk_id}`}
            citation={c}
            assistantId={assistantId}
            active={activeMarker === c.marker}
            onFocus={onFocus}
          />
        ))}
      </ul>
    </div>
  );
}

function SourceRow({
  citation,
  assistantId,
  active,
  onFocus,
}: {
  citation: Citation;
  assistantId: string;
  active: boolean;
  onFocus?: (marker: number | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);

  const loc = locLine(citation);

  const open_ = useCallback(async () => {
    // A web source already carries a complete href (original URL plus a
    // #:~:text= fragment). A file has to be resolved now, because the
    // presigned URL it needs is deliberately short-lived.
    if (citation.href) {
      window.open(citation.href, "_blank", "noopener,noreferrer");
      return;
    }
    if (!citation.data_source_id) return;
    setOpening(true);
    setOpenError(null);
    try {
      const res = await dataSources.contentUrl(assistantId, citation.data_source_id);
      if (!res.url) {
        setOpenError("This source has no file to open.");
        return;
      }
      const page = citation.loc?.page;
      // Browsers' built-in PDF viewers honour #page=N; harmless on anything else.
      const target = page != null ? `${res.url}#page=${page}` : res.url;
      window.open(target, "_blank", "noopener,noreferrer");
    } catch {
      setOpenError("Couldn't open this source.");
    } finally {
      setOpening(false);
    }
  }, [citation, assistantId]);

  // Pasted text has nothing to open, and an orphaned chunk (its data source
  // deleted) has nothing to resolve. Showing a dead button is worse than
  // showing none — the snippet below is the source in that case.
  const canOpen = Boolean(citation.href || citation.data_source_id);

  return (
    <li
      id={`citation-${citation.marker}`}
      className={cn("px-3 py-2 text-xs transition-colors", active && "bg-muted")}
      onMouseEnter={() => onFocus?.(citation.marker)}
      onMouseLeave={() => onFocus?.(null)}
    >
      <div className="flex items-start gap-2">
        <span className="bg-primary text-primary-foreground mt-px inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded px-1 text-[10px] font-medium tabular-nums">
          {citation.marker}
        </span>
        <div className="min-w-0 flex-1">
          <button
            onClick={() => setOpen((v) => !v)}
            className="w-full text-left"
            aria-expanded={open}
          >
            <span className="font-medium">{citation.title || "Untitled source"}</span>
            <span className="text-muted-foreground ml-2">{kindLabel(citation)}</span>
            {loc && <span className="text-muted-foreground ml-2">{loc}</span>}
          </button>
          {open && (
            <div className="mt-1.5 space-y-1.5">
              <p className="text-muted-foreground whitespace-pre-wrap">{citation.snippet}</p>
              <div className="flex items-center gap-3">
                {canOpen && (
                  <button
                    onClick={() => void open_()}
                    disabled={opening}
                    className="text-primary font-medium disabled:opacity-50"
                  >
                    {opening ? "Opening…" : "Open source"}
                  </button>
                )}
                <span className="text-muted-foreground">relevance {citation.score.toFixed(2)}</span>
              </div>
              {openError && <p className="text-destructive">{openError}</p>}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}
