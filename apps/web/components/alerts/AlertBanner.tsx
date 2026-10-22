"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { alerts as alertsApi, type AdminAlertItem } from "@/lib/api";

/** For admins (Phase 8.9): when something stops Chat for everyone, a line
 *  at the top of every page until it is fixed. Checked on opening and every
 *  minute; quiet when it can't be checked. */
export function AlertBanner({ orgId }: { orgId: string }) {
  const [open, setOpen] = useState<AdminAlertItem[]>([]);

  useEffect(() => {
    let gone = false;
    const check = () =>
      alertsApi
        .list(orgId, true)
        .then((rows) => {
          if (!gone) setOpen(rows);
        })
        .catch(() => {});
    void check();
    const timer = setInterval(() => void check(), 60_000);
    return () => {
      gone = true;
      clearInterval(timer);
    };
  }, [orgId]);

  if (open.length === 0) return null;
  const first = open[0];
  return (
    <p
      role="alert"
      className="border-destructive/40 bg-destructive/10 text-small border-b px-4 py-2 text-center"
    >
      <span className="font-medium">{first.what}.</span>{" "}
      {open.length > 1 ? `And ${open.length - 1} more. ` : ""}
      Chat may not be answering people.{" "}
      <Link href="/alerts" className="font-medium underline underline-offset-[3px]">
        See alerts
      </Link>
    </p>
  );
}
