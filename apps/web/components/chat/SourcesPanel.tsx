"use client";

import { ChevronRight, ExternalLink } from "lucide-react";
import { useCallback, useId, useState } from "react";

import { failureText } from "@/components/chat/error-text";
import { citationChipClass } from "@/components/chat/Markdown";
import { Button } from "@/components/ui/button";
import { dataSources, type Citation } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Where in its document a passage sits, as separate plain parts: the page
 *  range and the heading trail. Each part is left out when the format
 *  genuinely doesn't have it (a DOCX has headings but no pages, a PDF the
 *  reverse, pasted text neither). Character offsets are internal and are
 *  never shown. */
function locParts(c: Citation): string[] {
  const parts: string[] = [];
  const { page, page_end, breadcrumb } = c.loc ?? {};
  if (page != null) {
    parts.push(page_end != null && page_end > page ? `Pages ${page}–${page_end}` : `Page ${page}`);
  }
  if (breadcrumb?.length) parts.push(breadcrumb.join(" / "));
  return parts;
}

function kindLabel(c: Citation): string {
  switch (c.source_type) {
    case "file":
      return "File";
    case "url":
      return "Web page";
    case "text":
      return "Pasted text";
    default:
      return "Source";
  }
}

/** The passages an answer cites, as a ruled list under it. Each row's chip
 *  matches the chip in the answer, and hovering or focusing either one
 *  highlights both. */
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
  const headingId = useId();
  if (!citations.length) return null;
  return (
    <section aria-labelledby={headingId} className="flex max-w-[68ch] flex-col gap-2">
      <h3 id={headingId} className="text-h4 font-semibold">
        Sources <span className="num text-muted-foreground font-normal">{citations.length}</span>
      </h3>
      <ul className="border-border divide-border divide-y border-y">
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
    </section>
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
  const bodyId = useId();

  const loc = locParts(citation);

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
        setOpenError("This source has no file to open. The passage above is all there is.");
        return;
      }
      const page = citation.loc?.page;
      // Browsers' built-in PDF viewers honour #page=N; harmless on anything else.
      const target = page != null ? `${res.url}#page=${page}` : res.url;
      window.open(target, "_blank", "noopener,noreferrer");
    } catch (err) {
      setOpenError(failureText("Couldn't open this source.", err));
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
      className={cn(
        "scroll-mt-4 px-2 py-2 transition-colors duration-120 ease-out",
        active && "bg-muted",
      )}
      onMouseEnter={() => onFocus?.(citation.marker)}
      onMouseLeave={() => onFocus?.(null)}
      onFocus={() => onFocus?.(citation.marker)}
      onBlur={() => onFocus?.(null)}
    >
      <div className="flex items-start gap-3">
        <span className={cn(citationChipClass(active), "mt-0.5")} aria-hidden>
          {citation.marker}
        </span>
        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="group focus-visible:ring-ring flex w-full items-start gap-2 rounded-sm text-left focus-visible:ring-2 focus-visible:outline-none"
            aria-expanded={open}
            aria-controls={bodyId}
          >
            <span className="min-w-0 flex-1">
              <span className="sr-only">Source {citation.marker}: </span>
              <span className="block font-medium break-words">
                {citation.title || "Untitled source"}
              </span>
              <span className="text-small text-muted-foreground flex flex-wrap gap-x-3">
                <span>{kindLabel(citation)}</span>
                {loc.map((part) => (
                  <span key={part} className="num break-words">
                    {part}
                  </span>
                ))}
              </span>
            </span>
            <ChevronRight
              aria-hidden
              className="text-muted-foreground mt-0.5 size-4 shrink-0 transition-transform duration-120 ease-out group-aria-expanded:rotate-90 motion-reduce:transition-none"
            />
          </button>
          {open && (
            <div id={bodyId} className="mt-2 flex flex-col gap-2">
              <p className="text-muted-foreground break-words whitespace-pre-wrap">
                {citation.snippet}
              </p>
              {canOpen && (
                <div>
                  <Button
                    type="button"
                    variant="link"
                    size="sm"
                    onClick={() => void open_()}
                    disabled={opening}
                  >
                    <ExternalLink aria-hidden />
                    {opening ? "Opening…" : "Open source"}
                  </Button>
                </div>
              )}
              {openError && (
                <p role="alert" className="text-small text-destructive">
                  {openError}
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}
