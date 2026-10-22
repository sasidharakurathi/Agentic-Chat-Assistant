import { describe, expect, it } from "vitest";

import {
  approveRule,
  canEditAssistant,
  canRemove,
  chatUnavailable,
  isAdminRole,
  previewHref,
  reviewersOf,
  versionActions,
} from "./assistant-lifecycle";

describe("who may do what with an assistant (Phase 7b.1)", () => {
  it("admins and the owner review; members do not", () => {
    expect(["owner", "admin", "member", null].map(isAdminRole)).toEqual([true, true, false, false]);
  });

  it("its creator and admins may edit and preview it", () => {
    const mine = { created_by: "u1" };
    expect(canEditAssistant(mine, "u1", "member")).toBe(true);
    expect(canEditAssistant(mine, "u2", "member")).toBe(false);
    expect(canEditAssistant(mine, "u2", "admin")).toBe(true);
    expect(canEditAssistant({ created_by: null }, null, "member")).toBe(false);
  });
});

describe("whether Chat answers", () => {
  it("only with an approved version, and never while paused", () => {
    expect(chatUnavailable({ status: "draft", current_version_id: null })).toMatch(/approved/);
    expect(chatUnavailable({ status: "archived", current_version_id: "v" })).toMatch(/paused/);
    expect(chatUnavailable({ status: "published", current_version_id: "v" })).toBeNull();
  });
});

describe("a version's actions", () => {
  const as = (admin: boolean, editor: boolean, live = false) => ({ admin, editor, live });

  it("a submitted version: an admin approves or rejects, its builder withdraws", () => {
    const v = { status: "submitted" as const };
    expect(versionActions(v, as(true, true))).toEqual(["approve", "reject", "withdraw", "preview"]);
    expect(versionActions(v, as(false, true))).toEqual(["withdraw", "preview"]);
    expect(versionActions(v, as(false, false))).toEqual([]);
  });

  it("an approved version that isn't live can be rolled back to, by an admin", () => {
    const v = { status: "approved" as const };
    expect(versionActions(v, as(true, true))).toEqual(["make-live", "preview"]);
    expect(versionActions(v, as(true, true, true))).toEqual(["preview"]);
    expect(versionActions(v, as(false, true))).toEqual(["preview"]);
  });

  it("a rolled back or rejected version can only be looked at", () => {
    for (const status of ["rolled_back", "rejected", "superseded", "withdrawn"] as const) {
      expect(versionActions({ status }, as(true, true))).toEqual(["preview"]);
    }
  });

  it("previews the draft, or one version", () => {
    expect(previewHref("a1")).toBe("/assistants/a1/chat?preview=1");
    expect(previewHref("a1", 3)).toBe("/assistants/a1/chat?preview=1&version=3");
  });
});

describe("who may approve a version (Phase 7b.3)", () => {
  const base = { submittedBy: "u1", builtBy: "u1", reviewers: ["u1", "u2", "owner"] };

  it("anyone but whoever submitted it or built the assistant", () => {
    expect(approveRule({ ...base, me: "u2", role: "admin" })).toBe("allowed");
    expect(approveRule({ ...base, me: "u1", role: "admin" })).toBe("refused");
    expect(approveRule({ ...base, submittedBy: "u3", me: "u1", role: "admin" })).toBe("refused");
    expect(approveRule({ ...base, me: "u4", role: "member" })).toBe("refused");
  });

  it("the owner approves their own only with nobody else, and with a note", () => {
    const own = { submittedBy: "owner", builtBy: "owner", me: "owner", role: "owner" };
    expect(approveRule({ ...own, reviewers: ["owner", "u2"] })).toBe("refused");
    expect(approveRule({ ...own, reviewers: ["owner"] })).toBe("needs_note");
    expect(
      approveRule({
        ...own,
        me: "u1",
        role: "admin",
        submittedBy: "u1",
        reviewers: ["u1", "owner"],
      }),
    ).toBe("refused");
  });

  it("counts only active admins and owners", () => {
    expect(
      reviewersOf([
        { user_id: "a", role: "admin" },
        { user_id: "b", role: "admin", is_active: false },
        { user_id: "c", role: "member" },
        { user_id: "d", role: "owner", is_active: true },
      ]),
    ).toEqual(["a", "d"]);
  });

  it("a refused approver still sees Reject", () => {
    expect(
      versionActions(
        { status: "submitted" },
        { live: false, admin: true, editor: true, approve: "refused" },
      ),
    ).toEqual(["reject", "withdraw", "preview"]);
  });
});

describe("people (Phase 7b.4)", () => {
  it("the server's answer on who edits wins: co-editors are known there", () => {
    expect(canEditAssistant({ created_by: "u1", can_edit: true }, "u2", "member")).toBe(true);
    expect(canEditAssistant({ created_by: "u1", can_edit: false }, "u1", "member")).toBe(false);
  });

  it("who may remove whom", () => {
    const owner = { id: "o", role: "owner" };
    const admin = { id: "a", role: "admin" };
    expect(canRemove(owner, { user_id: "a", role: "admin" })).toBe(true);
    expect(canRemove(owner, { user_id: "o", role: "owner" })).toBe(false);
    expect(canRemove(admin, { user_id: "m", role: "member" })).toBe(true);
    expect(canRemove(admin, { user_id: "b", role: "admin" })).toBe(false);
    expect(canRemove({ id: "m", role: "member" }, { user_id: "x", role: "member" })).toBe(false);
  });
});
