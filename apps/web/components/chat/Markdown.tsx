"use client";

import { createContext, memo, useContext, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Citation } from "@/lib/api";
import { citeMarker, withCitationLinks } from "@/lib/markdown-citations";
import { cn } from "@/lib/utils";

type CitationStateValue = {
  activeMarker?: number | null;
  onFocus?: (marker: number | null) => void;
};

/** The hover/active chip state, handed to the link renderer by context so the
 *  renderers below can live at module scope. Renderers made inside the
 *  component are new component types on every render, which remounts the whole
 *  rendered Markdown (and every chip under the cursor) on each keystroke. */
const CitationState = createContext<CitationStateValue>({});

const REMARK_PLUGINS = [remarkGfm];

const COMPONENTS: Components = {
  a: function CitationLink({ href, children }) {
    const { activeMarker, onFocus } = useContext(CitationState);
    const marker = citeMarker(href);
    if (marker === null) {
      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary underline underline-offset-2"
        >
          {children}
        </a>
      );
    }
    return (
      <button
        type="button"
        onClick={() => {
          document
            .getElementById(`citation-${marker}`)
            ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
          onFocus?.(marker);
        }}
        onMouseEnter={() => onFocus?.(marker)}
        onMouseLeave={() => onFocus?.(null)}
        className={cn(
          "mx-px inline-flex h-4 min-w-4 items-center justify-center rounded px-1 align-baseline text-[10px] font-medium tabular-nums transition-colors",
          activeMarker === marker
            ? "bg-primary text-primary-foreground"
            : "bg-primary/15 text-primary hover:bg-primary/25",
        )}
        title={`Source ${marker}`}
      >
        {marker}
      </button>
    );
  },
  h1: ({ children }) => <h3 className="mt-3 mb-1 text-base font-semibold">{children}</h3>,
  h2: ({ children }) => <h4 className="mt-3 mb-1 text-sm font-semibold">{children}</h4>,
  h3: ({ children }) => <h5 className="mt-2 mb-1 text-sm font-semibold">{children}</h5>,
  p: ({ children }) => <p className="my-1.5 leading-relaxed">{children}</p>,
  ul: ({ children }) => <ul className="my-1.5 list-disc space-y-0.5 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-1.5 list-decimal space-y-0.5 pl-5">{children}</ol>,
  blockquote: ({ children }) => (
    <blockquote className="border-border text-muted-foreground my-2 border-l-2 pl-3">
      {children}
    </blockquote>
  ),
  code: ({ className: lang, children }) =>
    lang ? (
      <code className={cn(lang, "font-mono text-xs")}>{children}</code>
    ) : (
      <code className="bg-background/60 rounded px-1 py-0.5 font-mono text-[0.85em]">
        {children}
      </code>
    ),
  pre: ({ children }) => (
    <pre className="bg-background/60 border-border my-2 overflow-auto rounded-md border p-2">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="my-2 overflow-auto">
      <table className="w-full border-collapse text-left text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-border border-b px-2 py-1 font-medium">{children}</th>
  ),
  td: ({ children }) => <td className="border-border/50 border-b px-2 py-1">{children}</td>,
  hr: () => <hr className="border-border my-3" />,
};

/** Rendered Markdown for answers and retrieved passages.
 *
 *  react-markdown builds React elements and does not render raw HTML, so a
 *  model (or a retrieved document) cannot inject markup into the page.
 *  Citation markers arrive as `#cite-n` links (see `withCitationLinks`) and
 *  render as the same chips the sources panel highlights. */
export function Markdown({
  text,
  citations = [],
  activeMarker,
  onFocus,
  className,
}: {
  text: string;
  citations?: Citation[];
  activeMarker?: number | null;
  onFocus?: (marker: number | null) => void;
  className?: string;
}) {
  const source = citations.length ? withCitationLinks(text, citations) : text;

  const state = useMemo(() => ({ activeMarker, onFocus }), [activeMarker, onFocus]);

  return (
    <div className={cn("min-w-0 break-words", className)}>
      <CitationState.Provider value={state}>
        <ParsedMarkdown source={source} />
      </CitationState.Provider>
    </div>
  );
}

/** Parsing is the expensive part; it reruns only when the text changes. */
const ParsedMarkdown = memo(function ParsedMarkdown({ source }: { source: string }) {
  return (
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={COMPONENTS}>
      {source}
    </ReactMarkdown>
  );
});
