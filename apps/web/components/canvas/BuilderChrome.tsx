"use client";

import { Sparkles } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { problemSummary, publishBlockedReason } from "@/components/canvas/station";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Lamp } from "@/components/ui/lamp";
import { Loading } from "@/components/ui/loading";
import { BackLink } from "@/components/ui/page-header";
import { SectionHeading } from "@/components/ui/section-heading";
import { Tabs, tabPanelProps, type TabOption } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

/** The builder's layout pieces (docs/DESIGN.md section 7). They live here,
 *  not in the page, because the page sits under a `build/` folder, which
 *  the repository's .gitignore excludes and Tailwind therefore never scans:
 *  a class written only in the page is never generated. */

/** The whole builder: a full-height column under the app shell's mobile top
 *  bar (52px below 768px), so the canvas never overflows the screen. */
export function BuilderFrame({ children }: { children: ReactNode }) {
  return <div className="flex h-[calc(100dvh-52px)] flex-col md:h-dvh">{children}</div>;
}

export function BuilderLoading() {
  return (
    <div className="px-4 py-6 md:px-8">
      <Loading what="the assistant" />
    </div>
  );
}

const STATUS_WORD: Record<string, string> = { published: "Published", draft: "Draft" };

/** Two rows plus tabs: the back link; the name, its status, the save state
 *  (announced), the problem count, Chat and Publish; then the tab strip,
 *  which scrolls rather than wraps and fades the edge with more past it. */
export function BuilderHeader<T extends string>({
  name,
  status,
  saveText,
  saveFailed,
  errors,
  warnings,
  problemsId,
  onShowProblems,
  chatHref,
  onPublish,
  busy,
  alerts,
  onDismissAlerts,
  tabsId,
  tab,
  onTabChange,
  tabOptions,
}: {
  name: string;
  status: string;
  /** "Saving…", "Saved", "Not saved" or nothing yet. */
  saveText: string;
  saveFailed: boolean;
  errors: number;
  warnings: number;
  /** The problems list the count opens. */
  problemsId: string;
  onShowProblems: () => void;
  chatHref: string;
  onPublish: () => void;
  busy: boolean;
  /** Save and publish errors, each saying what happened and what to do. */
  alerts: string[];
  onDismissAlerts: () => void;
  tabsId: string;
  tab: T;
  onTabChange: (tab: T) => void;
  tabOptions: TabOption<T>[];
}) {
  const blocked = errors > 0;
  const tabsWrap = useRef<HTMLDivElement>(null);
  const [fade, setFade] = useState({ left: false, right: false });
  useEffect(() => {
    const list = tabsWrap.current?.querySelector<HTMLElement>('[role="tablist"]');
    if (!list) return;
    const update = () =>
      setFade({
        left: list.scrollLeft > 1,
        right: list.scrollLeft + list.clientWidth < list.scrollWidth - 1,
      });
    update();
    list.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(list);
    return () => {
      list.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, []);

  return (
    <header className="border-border shrink-0 border-b px-4 pt-3 md:px-6">
      <BackLink href="/assistants" label="Assistants" />
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-2 lg:flex-nowrap">
        {/* The name gets at least 16rem before the actions wrap below it. */}
        <div className="flex min-w-0 flex-[1_1_16rem] items-center gap-3">
          <h1 className="font-condensed text-h1 min-w-0 truncate font-semibold" title={name}>
            {name}
          </h1>
          <Badge variant={status === "published" ? "success" : "muted"}>
            {STATUS_WORD[status] ?? status}
          </Badge>
          <span
            role="status"
            aria-live="polite"
            className={cn(
              "text-small shrink-0 whitespace-nowrap",
              saveFailed ? "text-destructive" : "text-muted-foreground",
            )}
          >
            {saveText}
          </span>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {errors + warnings > 0 && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-controls={problemsId}
              onClick={onShowProblems}
            >
              <Lamp tone={errors > 0 ? "destructive" : "warning"} />
              {problemSummary(errors, warnings)}
            </Button>
          )}
          <Link href={chatHref} className={buttonVariants({ variant: "outline", size: "sm" })}>
            Chat
          </Link>
          {/* Blocked, Publish stays focusable so it can say why. */}
          <span className="group relative inline-flex">
            <Button
              type="button"
              size="sm"
              onClick={blocked ? undefined : onPublish}
              disabled={busy}
              aria-disabled={blocked || undefined}
              aria-describedby={blocked ? `${problemsId}-publish` : undefined}
              className={cn(blocked && "hover:bg-primary cursor-not-allowed opacity-50")}
            >
              Publish
            </Button>
            {blocked && (
              <span
                id={`${problemsId}-publish`}
                role="tooltip"
                className="border-border bg-card text-small text-foreground shadow-float pointer-events-none absolute top-full right-0 z-40 mt-1.5 hidden rounded-md border px-2 py-1 whitespace-nowrap group-focus-within:block group-hover:block"
              >
                {publishBlockedReason(errors)}
              </span>
            )}
          </span>
        </div>
      </div>
      {alerts.length > 0 && (
        <Alert className="mt-2">
          <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
            <div className="min-w-0 flex-1">
              {alerts.map((a) => (
                <p key={a}>{a}</p>
              ))}
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="-my-1 h-7"
              onClick={onDismissAlerts}
            >
              Dismiss
            </Button>
          </div>
        </Alert>
      )}
      <div
        ref={tabsWrap}
        className={cn(
          "mt-2",
          fade.left && "mask-l-from-[calc(100%-2rem)]",
          fade.right && "mask-r-from-[calc(100%-2rem)]",
        )}
      >
        <Tabs
          label="Builder view"
          idBase={tabsId}
          value={tab}
          onChange={onTabChange}
          options={tabOptions}
          className="-mx-3"
        />
      </div>
    </header>
  );
}

const PANEL_LAYOUT = {
  /** Fills the rest of the page and scrolls. */
  scroll: "min-h-0 flex-1 overflow-y-auto",
  /** Fills the rest of the page; the child scrolls itself. */
  stack: "flex min-h-0 flex-1 flex-col",
} as const;

/** The content of one builder tab, tied to its tab for assistive
 *  technology. Not a tab stop itself: every panel holds controls. */
export function TabPanel({
  tabsId,
  value,
  layout,
  children,
}: {
  tabsId: string;
  value: string;
  layout: keyof typeof PANEL_LAYOUT;
  children: ReactNode;
}) {
  return (
    <div {...tabPanelProps(tabsId, value)} tabIndex={-1} className={PANEL_LAYOUT[layout]}>
      {children}
    </div>
  );
}

/** The canvas tab: the canvas with its overlays, and the drawer as a right
 *  column. Overlays: Add capability and Suggest a pipeline at the top-left;
 *  Tidy up, the run and the problems in one column on the right, so none of
 *  them can overlap another or the drawer; zoom and Key at the bottom-left
 *  (inside the canvas). */
export function CanvasStage({
  tabsId,
  canvas,
  palette,
  onSuggest,
  onTidy,
  traceError,
  onDismissTraceError,
  trace,
  problems,
  drawer,
}: {
  tabsId: string;
  canvas: ReactNode;
  palette: ReactNode;
  onSuggest: () => void;
  onTidy: () => void;
  traceError: string | null;
  onDismissTraceError: () => void;
  trace: ReactNode;
  problems: ReactNode;
  drawer: ReactNode;
}) {
  return (
    <div
      {...tabPanelProps(tabsId, "canvas")}
      tabIndex={-1}
      className="relative flex min-h-0 flex-1"
    >
      <div className="group/stage relative min-w-0 flex-1">
        {canvas}

        {/* Top-left: add things. Leaves the bottom-left to zoom and Key. */}
        <div className="pointer-events-none absolute top-3 bottom-32 left-3 z-10 flex max-w-[calc(100%-1.5rem)] flex-wrap content-start items-start gap-2">
          {palette}
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="shadow-float pointer-events-auto"
            title="Describe the assistant and get a whole starter pipeline to look over"
            onClick={onSuggest}
          >
            <Sparkles aria-hidden />
            Suggest a pipeline
          </Button>
        </div>

        {/* Right: Tidy up, then the run, then the problems docked at the
            bottom. One column, so none of them can overlap another. Below
            640px it starts under the Add capability button, and steps aside
            while the palette is open, which needs the width. */}
        <div className="pointer-events-none absolute top-3 right-3 bottom-3 z-10 flex w-[min(20rem,calc(100%-1.5rem))] flex-col items-end gap-2 max-sm:top-14 max-sm:bottom-32 max-sm:group-has-[[data-palette-open]]/stage:hidden">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="shadow-float pointer-events-auto shrink-0"
            title="Arrange every node into columns by type, with the main line on one row"
            onClick={onTidy}
          >
            Tidy up
          </Button>
          {traceError && (
            <Alert className="shadow-float pointer-events-auto w-full shrink-0">
              <p>{traceError}</p>
              <Button
                type="button"
                variant="link"
                size="sm"
                className="mt-1"
                onClick={onDismissTraceError}
              >
                Dismiss
              </Button>
            </Alert>
          )}
          {trace && <div className="flex min-h-0 w-full shrink flex-col">{trace}</div>}
          <div className="min-h-0 flex-1" />
          {problems && <div className="flex min-h-0 w-full shrink flex-col">{problems}</div>}
        </div>
      </div>
      {drawer}
    </div>
  );
}

/** The compiled config, with a warning when it is not the canvas's. */
export function CompiledConfigView({ stale, children }: { stale: boolean; children: ReactNode }) {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6 md:px-8">
      <SectionHeading
        level={2}
        title="Compiled config"
        description="The configuration the canvas and settings compile to. Publishing saves this as a new version."
      />
      {stale && (
        // The config is recompiled only when the graph compiles, so after a
        // structural error this is the *last compilable* one, not the graph
        // on screen. It was shown with no hint of that. (Reference errors,
        // like a missing connection, do not stop compiling.)
        <Alert tone="warning">
          The canvas has a problem that stops it compiling, so this is the last configuration that
          compiled, not what the canvas shows now. Fix the problems to update it.
        </Alert>
      )}
      {children}
    </div>
  );
}
