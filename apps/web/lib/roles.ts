/** What each role reaches in the web app (Phase 8.1). The server decides;
 *  this only keeps an employee from landing on pages that would refuse
 *  them. */

export type Role = "owner" | "admin" | "member" | "employee";

export function isEmployee(role: string | null | undefined): boolean {
  return role === "employee";
}

/** Chat (its home and its conversation links, Phase 8.6), a conversation
 *  with an assistant that answers in Chat, a shared conversation, and your
 *  account. A preview
 *  (`?preview=1`) is the Studio's. */
export function employeeMayOpen(pathname: string, search = ""): boolean {
  if (pathname === "/chat" || pathname.startsWith("/chat/")) return true;
  if (pathname.startsWith("/account")) return true;
  // A conversation a colleague shared (Phase 9.4).
  if (pathname.startsWith("/shared/")) return true;
  // An assistant's approver may be an employee (Phase 9.1).
  if (pathname === "/approvals" || pathname.startsWith("/approvals/")) return true;
  if (/^\/assistants\/[^/]+\/chat\/?$/.test(pathname)) {
    return !new URLSearchParams(search).has("preview");
  }
  return false;
}

/** The roles an admin can give, lowest first, in words. */
export const ROLE_WORDS: Record<Role, string> = {
  employee: "Employee",
  member: "Builder",
  admin: "Admin",
  owner: "Owner",
};
