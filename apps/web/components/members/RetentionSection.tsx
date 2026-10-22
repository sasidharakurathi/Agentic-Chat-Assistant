"use client";

import { useEffect, useState } from "react";

import { DataSettings } from "@/components/members/DataSettings";
import { orgs } from "@/lib/api";

/** Loads the retention setting, then shows Keeping and erasing (Phase
 *  8.10). Admins only; the owner also sets holds and erases. */
export function RetentionSection({ orgId, owner }: { orgId: string; owner: boolean }) {
  const [days, setDays] = useState<number | null>(null);

  useEffect(() => {
    orgs
      .settings(orgId)
      .then((s) => setDays(s.retention_days ?? 365))
      .catch(() => setDays(null));
  }, [orgId]);

  if (days === null) return null;
  return <DataSettings orgId={orgId} owner={owner} retentionDays={days} />;
}
