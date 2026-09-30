"use client";

import { Gauge, LogOut, Menu, Users, Waypoints, X, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { BrandLockup, BrandMark } from "@/components/brand-mark";
import { ThemeSegmented, ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Loading } from "@/components/ui/loading";
import { Select } from "@/components/ui/select";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const NAV: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/assistants", label: "Assistants", icon: Waypoints },
  { href: "/usage", label: "Usage", icon: Gauge },
  { href: "/members", label: "Members", icon: Users },
];

/** The builder and chat need the width: the rail shrinks to icons there. */
const WORK_SURFACE = /^\/assistants\/[^/]+\/(build|chat)(\/|$)/;

export default function AppLayout({ children }: { children: ReactNode }) {
  const { ready, user } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (ready && !user) router.replace("/login");
  }, [ready, user, router]);

  // A link in the sheet navigates; the sheet should not stay over the page.
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  if (!ready || !user) {
    return (
      <div className="flex min-h-dvh items-center justify-center p-6">
        <Loading what="your workspace" />
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
      <main className="bg-background min-w-0 flex-1">{children}</main>
    </div>
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
  const { user, orgs, activeOrgId, setActiveOrg, logout } = useAuth();
  const orgId = useId();
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
        {NAV.map((item) => (
          <NavLink
            key={item.href}
            href={item.href}
            icon={item.icon}
            active={pathname.startsWith(item.href)}
            collapsed={collapsed}
            onNavigate={onNavigate}
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
              <span className="min-w-0 flex-1 truncate text-sm" title={user.email}>
                {user.email}
              </span>
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
  children,
}: {
  href: string;
  icon: LucideIcon;
  active: boolean;
  collapsed: boolean;
  onNavigate?: () => void;
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
