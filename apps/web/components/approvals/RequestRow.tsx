"use client";

import Link from "next/link";

import { Lamp, type LampTone } from "@/components/ui/lamp";
import type { DecidableApproval } from "@/lib/api";
import { requestLine } from "@/lib/approvals";
import { formatRelative } from "@/lib/format";

/** A request's state as a lamp: amber while it waits or runs, green when
 *  done, red when it failed, unlit otherwise (Phase 9.2). */
export function requestTone(status: string): LampTone {
  if (status === "succeeded") return "success";
  if (status === "failed") return "destructive";
  if (status === "pending" || status === "approved" || status === "executing") return "warning";
  return "off";
}

/** One request in a list: what it is, where it stands, who asked and
 *  where, and when. Opens its page. `who`: whose name to show (the asker in
 *  an approver's lists; nobody in My requests, which are all yours). */
export function RequestRow({
  request,
  mine = false,
  showAsker = true,
}: {
  request: DecidableApproval;
  mine?: boolean;
  showAsker?: boolean;
}) {
  const asker = request.asker_name || request.asker_email || "Someone";
  return (
    <li className="border-border border-b">
      <Link
        href={`/approvals/${request.id}`}
        className="hover:bg-muted focus-visible:ring-ring -mx-2 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md px-2 py-3 focus-visible:ring-2 focus-visible:outline-none"
      >
        <div className="min-w-0 flex-[1_1_18rem]">
          <p className="truncate font-medium">{request.title ?? request.tool_name}</p>
          <p className="text-small text-muted-foreground truncate">
            <span className="num">{request.reference}</span>
            {showAsker ? `, ${asker}` : ""}
            {request.assistant_name ? ` in ${request.assistant_name}` : ""},{" "}
            {formatRelative(request.created_at)}
          </p>
        </div>
        <span className="text-small inline-flex shrink-0 items-center gap-1.5">
          <Lamp tone={requestTone(request.status)} />
          {requestLine(request.status, request.tier, mine)}
        </span>
      </Link>
    </li>
  );
}
