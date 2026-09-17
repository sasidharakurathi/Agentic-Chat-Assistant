"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

export default function AppLayout({ children }: { children: ReactNode }) {
  const { ready, user, orgs, activeOrgId, setActiveOrg, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (ready && !user) router.replace("/login");
  }, [ready, user, router]);

  if (!ready || !user) {
    return <div className="text-muted-foreground p-10 text-sm">Loading…</div>;
  }

  return (
    <div className="flex min-h-screen">
      <aside className="border-border bg-muted/40 flex w-60 shrink-0 flex-col border-r px-3 py-4">
        <Link href="/assistants" className="font-serif px-2 text-base font-semibold tracking-tight">
          Assistant Studio
        </Link>
        <nav className="mt-6 flex flex-col gap-1">
          <NavLink href="/assistants" active={pathname.startsWith("/assistants")}>
            Assistants
          </NavLink>
        </nav>
        <div className="mt-auto flex flex-col gap-2">
          {orgs.length > 1 && (
            <Select
              value={activeOrgId ?? ""}
              onChange={(e) => setActiveOrg(e.target.value)}
              className="h-8 text-xs"
            >
              {orgs.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </Select>
          )}
          <div className="text-muted-foreground truncate px-2 text-xs">{user.email}</div>
          <Button variant="outline" size="sm" onClick={logout}>
            Sign out
          </Button>
        </div>
      </aside>
      <main className="bg-background min-w-0 flex-1">{children}</main>
    </div>
  );
}

function NavLink({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: ReactNode;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "rounded-md px-2 py-1.5 text-sm transition-colors",
        active
          ? "bg-primary/10 text-primary font-medium"
          : "text-muted-foreground hover:text-foreground hover:bg-muted",
      )}
    >
      {children}
    </Link>
  );
}
