/** The audit log in words (Phase 7b.7): each recorded action as a short
 *  sentence an admin can scan, with the action's name kept for anything
 *  not listed here. */

const WORDS: Record<string, string> = {
  "user.register": "Created an account",
  "user.login": "Signed in",
  "user.login_failed": "Failed to sign in",
  "user.logout": "Signed out",
  "user.password_change": "Changed their password",
  "user.password_reset_requested": "Asked for a password reset",
  "user.password_reset": "Reset their password",
  "user.password_reset_link_issued": "Was given a reset link by the operator",
  "user.email_verified": "Confirmed their email",
  "user.identity.link": "Connected Google",
  "user.identity.unlink": "Disconnected Google",
  "user.jit_provision": "Joined with Google (auto-join)",
  "user.two_factor.enable": "Turned on two-step sign-in",
  "privacy_notice.accept": "Accepted how Chat uses their data",
  "privacy_notice.update": "Changed the privacy notice",
  "user.device_code.create": "Made a code to sign in on another device",
  "user.two_factor.disable": "Turned off two-step sign-in",
  "user.two_factor.verify": "Confirmed it was them with a code",
  "user.two_factor.recovery_codes": "Made new recovery codes",
  "user.two_factor.reset": "Two-step sign-in was turned off by the operator",
  "org.create": "Created the organisation",
  "org.settings.update": "Changed the organisation's settings",
  "org.archive": "The organisation was archived",
  "org.unarchive": "The organisation was reopened",
  "org.became_team": "A personal organisation became the team's",
  "org.member.role_change": "Changed someone's role",
  "org.member.deactivate": "Removed someone",
  "org.member.reactivate": "Reactivated someone",
  "org.invite.create": "Invited someone",
  "org.invite.accept": "Joined through an invite",
  "org.invite.revoke": "Withdrew an invite",
  "conversation.read_by_admin": "Opened someone's conversation",
  "conversation.rename": "Renamed a conversation",
  "conversation.archive": "Archived a conversation",
  "assistant.create": "Created an assistant",
  "assistant.update": "Renamed or redescribed an assistant",
  "assistant.delete": "Deleted an assistant",
  "assistant.transfer": "An assistant passed to someone else",
  "assistant.archive": "Paused an assistant",
  "assistant.unarchive": "Reopened an assistant",
  "assistant.official": "Marked an assistant Official, or not",
  "group.create": "Made a group",
  "group.update": "Renamed or redescribed a group",
  "group.delete": "Deleted a group",
  "group.member.add": "Added someone to a group",
  "group.member.remove": "Took someone out of a group",
  "assistant.editor.add": "Added a co-editor",
  "assistant.editor.remove": "Removed a co-editor",
  "assistant.version.submit": "Submitted a version for review",
  "assistant.version.approve": "Approved a version",
  "assistant.version.reject": "Rejected a version",
  "assistant.version.withdraw": "Withdrew a version",
  "assistant.version.rollback": "Rolled back to an earlier version",
  "assistant.version.grandfathered": "Versions approved by the upgrade",
  "assistant.version.manifest_recorded": "A version's manifest was recorded",
  "approval.approved": "Approved a tool call",
  "approval.denied": "Declined a tool call",
  "approval.expired": "A tool call's approval expired",
  "data_source.create": "Added a knowledge source",
  "data_source.delete": "Deleted a knowledge source",
  "data_source.reindex": "Reindexed a knowledge source",
  "db_connection.create": "Added a database connection",
  "db_connection.update": "Changed a database connection",
  "db_connection.delete": "Deleted a database connection",
  "mcp_server.create": "Added an MCP server",
  "mcp_server.update": "Changed an MCP server",
  "mcp_server.delete": "Deleted an MCP server",
  "mcp_server.discover": "Refreshed an MCP server's tools",
  "memory.clear": "Cleared an assistant's memory",
  "eval_suite.create": "Created an eval suite",
  "eval_suite.update": "Changed an eval suite",
  "eval_suite.delete": "Deleted an eval suite",
  "eval_suite.cases": "Changed an eval suite's cases",
  "eval_run.start": "Started an eval run",
  "eval_run.cancel": "Cancelled an eval run",
  "budget.updated": "Changed a budget",
  "mail.test": "Sent themselves a test email",
  "audit.export": "Exported the audit log",
  "analytics.export": "Downloaded the assistants report",
};

export function describeAction(action: string): string {
  return WORDS[action] ?? action;
}

/** What the page filters by: a prefix of the action's name. */
export const AUDIT_KINDS: { value: string; label: string }[] = [
  { value: "", label: "Everything" },
  { value: "conversation.read_by_admin", label: "Admins opening conversations" },
  { value: "assistant.", label: "Assistants and versions" },
  { value: "approval.", label: "Tool-call approvals" },
  { value: "org.", label: "People and invites" },
  { value: "user.", label: "Sign-ins and accounts" },
  { value: "data_source.", label: "Knowledge" },
  { value: "db_connection.", label: "Database connections" },
  { value: "mcp_server.", label: "MCP servers" },
  { value: "eval_", label: "Evals" },
  { value: "audit.", label: "The audit log itself" },
];

/** The one detail worth showing in the row, when there is one. */
export function auditDetail(action: string, meta: Record<string, unknown>): string | null {
  if (action === "conversation.read_by_admin" && typeof meta.reason === "string") {
    return `Reason: ${meta.reason}`;
  }
  if (action === "user.login_failed" && typeof meta.reason === "string") {
    return meta.reason === "account_disabled" ? "Account switched off" : "Wrong password";
  }
  if (action === "assistant.version.approve" && meta.self_approved === true) {
    return "Self-approved: nobody else could review";
  }
  if ((action === "assistant.archive" || action === "org.member.deactivate") && meta.reason) {
    return `Reason: ${String(meta.reason)}`;
  }
  if (typeof meta.version === "number") return `Version ${meta.version}`;
  return null;
}
