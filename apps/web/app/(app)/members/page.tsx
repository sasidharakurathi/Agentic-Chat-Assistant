"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { ApiError, invites, orgs, type Member } from "@/lib/api";
import { useAuth } from "@/lib/auth";

/** Who is in the active organization, and — for admins — a way to invite
 *  someone. There is no email delivery: the invite link is shown once, to be
 *  sent however the admin likes. It is the only copy (the server keeps just
 *  a hash of the token), so it cannot be shown again later. */
export default function MembersPage() {
  const { activeOrgId, activeRole } = useAuth();
  const [rows, setRows] = useState<Member[]>([]);
  const [more, setMore] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Member["role"]>("member");
  const [sending, setSending] = useState(false);
  const [link, setLink] = useState<{ email: string; url: string; expires: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const canInvite = activeRole === "owner" || activeRole === "admin";

  const load = useCallback(async () => {
    if (!activeOrgId) return;
    setError(null);
    try {
      const page = await orgs.members(activeOrgId);
      setRows(page.items);
      setMore(page.next_cursor);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load members");
    }
  }, [activeOrgId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function loadMore() {
    if (!activeOrgId || !more) return;
    const page = await orgs.members(activeOrgId, more);
    setRows((r) => [...r, ...page.items]);
    setMore(page.next_cursor);
  }

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    if (!activeOrgId || !email.trim()) return;
    setSending(true);
    setError(null);
    setCopied(false);
    try {
      const created = await invites.create(activeOrgId, email.trim(), role);
      setLink({
        email: created.email,
        url: created.accept_url,
        expires: new Date(created.expires_at).toLocaleString(),
      });
      setEmail("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the invite");
    } finally {
      setSending(false);
    }
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied(true);
    } catch {
      /* the link is selectable text as well */
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-8 py-10">
      <h1 className="font-serif text-2xl font-semibold tracking-tight">Members</h1>
      <p className="text-muted-foreground mt-1 text-sm">People in this organization.</p>

      {canInvite && (
        <form onSubmit={invite} className="mt-6 flex flex-wrap items-end gap-3">
          <div className="flex min-w-60 flex-1 flex-col gap-1.5">
            <Label htmlFor="invite-email">Invite by email</Label>
            <Input
              id="invite-email"
              type="email"
              required
              placeholder="teammate@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="invite-role">Role</Label>
            <Select
              id="invite-role"
              value={role}
              onChange={(e) => setRole(e.target.value as Member["role"])}
            >
              <option value="member">member</option>
              <option value="admin">admin</option>
            </Select>
          </div>
          <Button type="submit" disabled={sending}>
            {sending ? "Creating…" : "Create invite link"}
          </Button>
        </form>
      )}

      {link && (
        <div className="border-border bg-muted/40 mt-4 rounded-lg border p-3 text-sm">
          <p>
            Invite link for <strong>{link.email}</strong>. Send it to them yourself. It is shown
            only once, and expires {link.expires}.
          </p>
          <div className="mt-2 flex items-center gap-2">
            <code className="bg-background flex-1 truncate rounded px-2 py-1 text-xs select-all">
              {link.url}
            </code>
            <Button type="button" size="sm" variant="outline" onClick={() => void copy()}>
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
        </div>
      )}

      {error && (
        <p className="text-destructive mt-4 text-sm" role="alert">
          {error}
        </p>
      )}

      <ul className="border-border mt-6 divide-y rounded-lg border">
        {rows.map((m) => (
          <li key={m.user_id} className="flex items-center justify-between px-4 py-3 text-sm">
            <div>
              <div className="font-medium">{m.name || m.email}</div>
              <div className="text-muted-foreground text-xs">{m.email}</div>
            </div>
            <Badge variant={m.role === "member" ? "muted" : "success"}>{m.role}</Badge>
          </li>
        ))}
      </ul>
      {more && (
        <Button variant="ghost" size="sm" className="mt-2" onClick={() => void loadMore()}>
          Load more
        </Button>
      )}
    </div>
  );
}
