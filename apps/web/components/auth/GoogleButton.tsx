"use client";

import { useEffect, useState } from "react";

import { buttonVariants } from "@/components/ui/button";
import { auth } from "@/lib/api";
import { googleStartUrl } from "@/lib/google";
import { cn } from "@/lib/utils";

/** "Continue with Google" (Phase 8.3), when the server has Google sign-in
 *  set up; nothing otherwise. A link, not a fetch: the browser itself goes
 *  to Google and back. */
export function GoogleButton({
  next,
  invite,
  label = "Continue with Google",
  className,
}: {
  next?: string | null;
  invite?: string | null;
  label?: string;
  className?: string;
}) {
  const [domain, setDomain] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    auth
      .providers()
      .then((p) => setDomain(p.google ? (p.google_domain ?? null) : undefined))
      .catch(() => setDomain(undefined));
  }, []);

  if (domain === undefined) return null;
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <a href={googleStartUrl({ next, invite })} className={buttonVariants({ variant: "outline" })}>
        {label}
      </a>
      {domain && (
        <p className="text-small text-muted-foreground">With your {domain} Google account.</p>
      )}
    </div>
  );
}
