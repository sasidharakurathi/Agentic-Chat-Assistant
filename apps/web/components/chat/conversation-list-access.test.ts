/** The conversation list offers Rename and Archive only on conversations the
 *  signed-in user started (Phase 7a.4): the API refuses them on anyone
 *  else's, admins included. Rendered for real, to HTML. */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Conversation } from "@/lib/api";

import { ConversationList } from "./ConversationList";

function row(id: string, title: string, createdBy: string | null): Conversation {
  return {
    id,
    assistant_id: "a1",
    assistant_version_id: null,
    title,
    created_by: createdBy,
    status: "active",
    cost_usd: "0",
    token_usage: {},
    created_at: new Date().toISOString(),
    last_message_at: null,
    running: false,
  } as Conversation;
}

function render(userId: string | null): string {
  const noop = () => {};
  return renderToStaticMarkup(
    createElement(ConversationList, {
      assistantId: "a1",
      name: "Helpdesk",
      rows: [row("c1", "Mine", "me"), row("c2", "Asha's", "asha"), row("c3", "Orphan", null)],
      userId,
      active: "c2",
      more: false,
      onSelect: noop,
      onNewChat: noop,
      onRename: noop,
      onArchive: noop,
      onLoadMore: noop,
    }),
  );
}

describe("the conversation list", () => {
  it("offers Rename and Archive on the user's own conversation", () => {
    const html = render("me");
    expect(html).toContain('aria-label="Rename Mine"');
    expect(html).toContain('aria-label="Archive Mine"');
  });

  it("offers neither on someone else's, even the open one, or on one whose starter is gone", () => {
    const html = render("me");
    for (const title of ["Asha&#x27;s", "Orphan"]) {
      expect(html).not.toContain(`aria-label="Rename ${title}"`);
      expect(html).not.toContain(`aria-label="Archive ${title}"`);
    }
    // Still listed, so it can be read.
    expect(html).toContain("Asha&#x27;s");
  });

  it("offers nothing before the signed-in user is known", () => {
    expect(render(null)).not.toContain('aria-label="Rename');
  });
});
