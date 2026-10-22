"use client";

import {
  BellRing,
  Gauge,
  LogOut,
  Menu,
  MessagesSquare,
  ScrollText,
  ShieldCheck,
  Users,
  UsersRound,
  Waypoints,
  X,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { AlertBanner } from "@/components/alerts/AlertBanner";
import { BrandLockup, BrandMark } from "@/components/brand-mark";
import { NoticeGate } from "@/components/privacy/NoticeGate";
import { ThemeSegmented, ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Loading } from "@/components/ui/loading";
import { Select } from "@/components/ui/select";
import { chat } from "@/lib/api";
import { safeNext, useAuth } from "@/lib/auth";
import { rememberOfflineHelp } from "@/lib/offline-help";
import { employeeMayOpen, isEmployee } from "@/lib/roles";
import { useApprovalSummary } from "@/lib/use-approval-summary";
import { cn } from "@/lib/utils";

const NAV: {
  href: string;
  label: string;
  icon: LucideIcon;
  admins?: boolean;
  /** The Studio's: not for the Employee role (Phase 8.1). */
  studio?: boolean;
}[] = [
  { href: "/chat", label: "Chat", icon: MessagesSquare },
  { href: "/assistants", label: "Assistants", icon: Waypoints, studio: true },
  { href: "/usage", label: "Usage", icon: Gauge, studio: true },
  { href: "/members", label: "Members", icon: Users, studio: true },
  // Phase 8.5: who an assistant can be for.
  { href: "/groups", label: "Groups", icon: UsersRound, admins: true },
  // Phase 9.1: the requests waiting for you, an admin or a named approver.
  { href: "/approvals", label: "Approvals", icon: ShieldCheck },
  // Phase 7b.7: admins and the owner only (the API refuses everyone else).
  { href: "/audit", label: "Audit log", icon: ScrollText, admins: true },
  // Phase 8.9: what stopped Chat for everyone.
  { href: "/alerts", label: "Alerts", icon: BellRing, admins: true },
];

/** Chat's own pages (Phase 8.7): they wait for the privacy notice. A
 *  preview is the Studio's; Privacy & help shows the notice itself. */
function chatPage(pathname: string, search: string): boolean {
  if (pathname === "/chat/privacy") return false;
  if (pathname === "/chat" || pathname.startsWith("/chat/")) return true;
  return (
    /^\/assistants\/[^/]+\/chat\/?$/.test(pathname) && !new URLSearchParams(search).has("preview")
  );
}

/** The builder and chat need the width: the rail shrinks to icons there. */
const WORK_SURFACE = /^\/assistants\/[^/]+\/(build|chat)(\/|$)/;

export default function AppLayout({ children }: { children: ReactNode }) {
  const { ready, user, offline, refresh, activeRole, activeOrgId, twoFactor } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  // An employee chats (Phase 8.1): any other page would only be refused by
  // the server, so they are taken to Chat instead.
  useEffect(() => {
    if (!ready || !isEmployee(activeRole)) return;
    if (!employeeMayOpen(pathname, window.location.search)) router.replace("/chat");
  }, [ready, activeRole, pathname, router]);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    // Offline is not signed out (Phase 7a.8): stay, and keep trying. Signed
    // out, the page asked for comes back after signing in (Phase 8.6): a
    // link from an email used to land on the assistants list instead.
    if (!ready || user || offline) return;
    const back = safeNext(pathname + window.location.search);
    router.replace(back ? `/login?next=${encodeURIComponent(back)}` : "/login");
  }, [ready, user, offline, router, pathname]);

  // The organisation's steps for getting on its network, kept for the
  // offline page: the IT handbook sits behind the same VPN (Phase 8.6).
  useEffect(() => {
    if (!ready || !user || !activeOrgId) return;
    chat
      .help()
      .then(rememberOfflineHelp)
      .catch(() => {});
  }, [ready, user, activeOrgId]);

  // A link in the sheet navigates; the sheet should not stay over the page.
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  if (offline && !user) {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-4 p-6 text-center">
        <p role="status" className="max-w-sm">
          Can&apos;t reach the server right now. You&apos;re still signed in, and this page tries
          again by itself.
        </p>
        <Button variant="outline" onClick={() => void refresh()}>
          Try now
        </Button>
      </div>
    );
  }

  if (!ready || !user) {
    return (
      <div className="flex min-h-dvh items-center justify-center p-6">
        <Loading what="your workspace" />
      </div>
    );
  }

  const page =
    typeof window !== "undefined" && chatPage(pathname, window.location.search) ? (
      <NoticeGate>{children}</NoticeGate>
    ) : (
      children
    );

  // An employee's Chat is its own app (Phase 8.6): a slim bar, no Studio.
  if (isEmployee(activeRole)) {
    return (
      <div className="flex min-h-dvh flex-col">
        <ChatBar />
        <main className="bg-background min-w-0 flex-1">
          {offline && (
            <p
              role="status"
              className="border-border bg-muted text-small border-b px-4 py-2 text-center"
            >
              Can&apos;t reach the server right now. Trying again.
            </p>
          )}
          {page}
        </main>
      </div>
    );
  }

  const collapsed = WORK_SURFACE.test(pathname);

  return (
    <div className="flex min-h-dvh flex-col md:flex-row">
      {/* Below 768px: a top bar, with the rail's content in a left sheet. */}
      <header className="border-border bg-background sticky top-0 z-30 flex h-[52px] shrink-0 items-center gap-2 border-b px-2 md:hidden">
        <Button
          variant="ghost"
          size="icon"
          aria-label="Open menu"
          aria-expanded={menuOpen}
          aria-haspopup="dialog"
          onClick={() => setMenuOpen(true)}
          className="size-10"
        >
          <Menu aria-hidden className="size-5" />
        </Button>
        <Link
          href="/assistants"
          className="focus-visible:ring-ring rounded-md px-1 focus-visible:ring-2 focus-visible:outline-none"
        >
          <BrandLockup />
        </Link>
      </header>
      <MenuSheet open={menuOpen} onClose={() => setMenuOpen(false)} pathname={pathname} />

      <aside
        className={cn(
          "border-border bg-background sticky top-0 z-30 hidden h-dvh shrink-0 flex-col border-r md:flex",
          collapsed ? "w-16 items-center px-2 py-4" : "w-[232px] overflow-y-auto px-3 py-4",
        )}
      >
        <RailContent pathname={pathname} collapsed={collapsed} />
      </aside>
      <main className="bg-background min-w-0 flex-1">
        {offline && (
          <p
            role="status"
            className="border-border bg-muted text-small border-b px-4 py-2 text-center"
          >
            Can&apos;t reach the server right now. Trying again.
          </p>
        )}
        {/* Phase 8.9: something stopped Chat for everyone. */}
        {(activeRole === "owner" || activeRole === "admin") && activeOrgId && (
          <AlertBanner orgId={activeOrgId} />
        )}
        {/* Phase 8.4: admin features wait for a second factor. */}
        {twoFactor?.required && !twoFactor.session_verified && pathname !== "/account" && (
          <p
            role="status"
            className="border-border bg-muted text-small border-b px-4 py-2 text-center"
          >
            Admin features need two-step sign-in.{" "}
            <Link href="/account" className="font-medium underline underline-offset-[3px]">
              {twoFactor.enabled ? "Confirm it's you" : "Set it up"}
            </Link>{" "}
            in Your account.
          </p>
        )}
        {page}
      </main>
    </div>
  );
}

/** The Chat app's bar, for employees (Phase 8.6): Chat, your account and
 *  signing out, at every width. 52px, like the Studio's phone bar, so the
 *  chat page's height is worked out the same way. */
function ChatBar() {
  const { user, logout } = useAuth();
  // An employee can be an assistant's approver (Phase 9.1): the link shows
  // when something waits for them, with how many (9.2).
  const summary = useApprovalSummary(Boolean(user));
  const toDecide = summary?.to_decide ?? 0;
  if (!user) return null;
  return (
    <header className="border-border bg-background sticky top-0 z-30 flex h-[52px] shrink-0 items-center gap-2 border-b px-3 md:px-6">
      <Link
        href="/chat"
        aria-label="Chat home"
        className="focus-visible:ring-ring rounded-md px-1 focus-visible:ring-2 focus-visible:outline-none"
      >
        <BrandLockup />
      </Link>
      <div className="ml-auto flex items-center gap-1">
        {toDecide > 0 && (
          <Link
            href="/approvals"
            className="hover:bg-muted focus-visible:ring-ring flex h-9 items-center rounded-md px-2 text-sm focus-visible:ring-2 focus-visible:outline-none"
          >
            Approvals <span className="num text-muted-foreground ml-1">{toDecide}</span>
          </Link>
        )}
        <Link
          href="/chat/privacy"
          className="hover:bg-muted focus-visible:ring-ring flex h-9 items-center rounded-md px-2 text-sm focus-visible:ring-2 focus-visible:outline-none"
        >
          <ShieldCheck aria-hidden className="size-4 sm:hidden" />
          <span className="sr-only sm:not-sr-only">Privacy &amp; help</span>
        </Link>
        <ThemeToggle iconOnly />
        <Link
          href="/account"
          title={`${user.email}: your account`}
          className="hover:bg-muted focus-visible:ring-ring flex h-9 items-center gap-2 rounded-md px-2 text-sm focus-visible:ring-2 focus-visible:outline-none"
        >
          <span
            aria-hidden
            className="bg-muted text-foreground text-small inline-flex size-7 shrink-0 items-center justify-center rounded-full font-semibold"
          >
            {initials(user.name, user.email)}
          </span>
          <span className="hidden sm:inline">Your account</span>
          <span className="sr-only sm:hidden">Your account</span>
        </Link>
        <Button variant="ghost" size="sm" className="px-2" onClick={() => logout()}>
          Sign out
        </Button>
      </div>
    </header>
  );
}

/** Brand, nav and footer. Shared by the rail and the mobile sheet. */
function RailContent({
  pathname,
  collapsed,
  onNavigate,
}: {
  pathname: string;
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const { user, orgs, activeOrgId, activeRole, setActiveOrg, logout } = useAuth();
  const admin = activeRole === "owner" || activeRole === "admin";
  const orgId = useId();
  // Requests waiting for this person to decide (Phase 9.2).
  const summary = useApprovalSummary(Boolean(user));
  if (!user) return null;
  const activeOrg = orgs.find((o) => o.id === activeOrgId) ?? orgs[0];

  return (
    <>
      <Link
        href="/assistants"
        onClick={onNavigate}
        aria-label={collapsed ? "Assistant Studio" : undefined}
        title={collapsed ? "Assistant Studio" : undefined}
        className={cn(
          "focus-visible:ring-ring flex h-10 items-center rounded-md focus-visible:ring-2 focus-visible:outline-none",
          collapsed ? "w-10 justify-center" : "px-2",
        )}
      >
        {collapsed ? <BrandMark className="h-[14px] w-6" /> : <BrandLockup />}
      </Link>

      <nav aria-label="Main" className={cn("mt-6 flex flex-col gap-1", !collapsed && "w-full")}>
        {NAV.filter(
          (item) => (!item.admins || admin) && (!item.studio || !isEmployee(activeRole)),
        ).map((item) => (
          <NavLink
            key={item.href}
            href={item.href}
            icon={item.icon}
            active={pathname.startsWith(item.href)}
            collapsed={collapsed}
            onNavigate={onNavigate}
            count={item.href === "/approvals" ? summary?.to_decide : undefined}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className={cn("mt-auto flex flex-col pt-6", collapsed ? "items-center gap-2" : "gap-4")}>
        {collapsed ? (
          <ThemeToggle iconOnly />
        ) : (
          <>
            <div className="flex flex-col gap-1.5 px-1">
              {orgs.length > 1 ? (
                <>
                  <label htmlFor={orgId} className="text-small text-muted-foreground">
                    Organization
                  </label>
                  <Select
                    id={orgId}
                    value={activeOrgId ?? ""}
                    onChange={(e) => setActiveOrg(e.target.value)}
                    className="h-8"
                  >
                    {orgs.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.name}
                      </option>
                    ))}
                  </Select>
                </>
              ) : (
                activeOrg && (
                  <>
                    <span className="text-small text-muted-foreground">Organization</span>
                    <span className="truncate text-sm font-medium" title={activeOrg.name}>
                      {activeOrg.name}
                    </span>
                  </>
                )
              )}
            </div>
            <div className="flex flex-col gap-1.5 px-1">
              <span className="text-small text-muted-foreground" aria-hidden>
                Theme
              </span>
              <ThemeSegmented className="w-full" />
            </div>
          </>
        )}

        <div
          className={cn(
            "border-border flex items-center border-t pt-3",
            collapsed ? "flex-col gap-2" : "gap-2 px-1",
          )}
        >
          <span
            aria-hidden
            title={collapsed ? user.email : undefined}
            className="bg-muted text-foreground text-small inline-flex size-8 shrink-0 items-center justify-center rounded-full font-semibold"
          >
            {initials(user.name, user.email)}
          </span>
          {collapsed ? (
            <Button
              variant="ghost"
              size="icon"
              aria-label="Sign out"
              title="Sign out"
              onClick={() => logout()}
            >
              <LogOut aria-hidden />
            </Button>
          ) : (
            <>
              <Link
                href="/account"
                className="hover:text-foreground focus-visible:ring-ring min-w-0 flex-1 truncate rounded-sm text-sm focus-visible:ring-2 focus-visible:outline-none"
                title={`${user.email}: your account`}
              >
                {user.email}
              </Link>
              <Button variant="ghost" size="sm" className="px-2" onClick={() => logout()}>
                Sign out
              </Button>
            </>
          )}
        </div>
      </div>
    </>
  );
}

function NavLink({
  href,
  icon: Icon,
  active,
  collapsed,
  onNavigate,
  count,
  children,
}: {
  href: string;
  icon: LucideIcon;
  active: boolean;
  collapsed: boolean;
  onNavigate?: () => void;
  /** How many wait here (Phase 9.2): shown when more than none. */
  count?: number;
  children: string;
}) {
  return (
    <Link
      href={href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group focus-visible:ring-ring relative flex h-9 items-center rounded-md text-sm transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none",
        collapsed ? "w-10 justify-center" : "gap-3 px-3",
        active
          ? "bg-muted text-foreground before:bg-marker font-semibold before:absolute before:inset-y-1.5 before:left-0 before:w-[3px] before:rounded-full"
          : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      <Icon aria-hidden className="size-4 shrink-0" />
      <span className={cn(collapsed && "sr-only")}>{children}</span>
      {count ? (
        collapsed ? (
          <span aria-hidden className="bg-warning absolute top-1.5 right-1.5 size-2 rounded-full" />
        ) : (
          <span className="num text-small text-foreground ml-auto">
            {count}
            <span className="sr-only"> waiting</span>
          </span>
        )
      ) : null}
      {collapsed && (
        <span
          aria-hidden
          className="bg-card border-border text-foreground shadow-float text-label pointer-events-none absolute top-1/2 left-full z-40 ml-3 -translate-y-1/2 rounded-md border px-2 py-1 font-medium whitespace-nowrap opacity-0 transition-opacity duration-120 ease-out group-hover:opacity-100 group-focus-visible:opacity-100"
        >
          {children}
        </span>
      )}
    </Link>
  );
}

/** The mobile menu: a left sheet on a native modal <dialog>, which traps
 *  focus, closes on Esc and returns focus to the menu button. */
function MenuSheet({
  open,
  onClose,
  pathname,
}: {
  open: boolean;
  onClose: () => void;
  pathname: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  // Growing past 768px swaps the sheet for the rail; don't leave a modal open.
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    const onChange = () => {
      if (mq.matches) onClose();
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [onClose]);

  return (
    <dialog
      ref={ref}
      aria-label="Menu"
      onClose={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      className="bg-background text-foreground border-border shadow-float backdrop:bg-scrim open:animate-sheet-in fixed inset-y-0 left-0 m-0 h-dvh max-h-none w-[min(18rem,calc(100vw-3rem))] max-w-none border-r p-0"
    >
      <div className="flex h-full flex-col overflow-y-auto px-3 py-3">
        <div className="flex justify-end">
          <Button variant="ghost" size="icon" aria-label="Close menu" onClick={onClose}>
            <X aria-hidden />
          </Button>
        </div>
        <RailContent pathname={pathname} collapsed={false} onNavigate={onClose} />
      </div>
    </dialog>
  );
}

function initials(name: string | null | undefined, email: string): string {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean);
  const letters =
    words.length >= 2
      ? words[0][0] + words[words.length - 1][0]
      : (words[0]?.[0] ?? email[0] ?? "");
  return letters.toUpperCase();
}
