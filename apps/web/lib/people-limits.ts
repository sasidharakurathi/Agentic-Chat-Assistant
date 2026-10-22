/** Per-person limits in words, for the admins (Phase 8.9). */

import { formatUsd } from "@/lib/format";

/** A person's cap for one period: "$2.00", marked when it is their own
 *  rather than their role's; "No limit" when neither sets one. */
export function limitWords(limit: number | null | undefined, own: boolean): string {
  if (limit === null || limit === undefined) return "No limit";
  return own ? `${formatUsd(limit)} (their own)` : formatUsd(limit);
}

/** How much of a cap is spent, as a whole percentage; null with no cap. */
export function usedShare(spent: number, limit: number | null | undefined): number | null {
  if (!limit) return null;
  return Math.min(999, Math.round((spent / limit) * 100));
}

/** The role a default covers, in words. */
export const DEFAULT_ROLES: { role: "employee" | "builder"; label: string }[] = [
  { role: "employee", label: "Each Employee" },
  { role: "builder", label: "Each builder, admin and the owner" },
];
