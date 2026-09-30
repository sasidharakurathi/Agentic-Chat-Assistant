"use client";

import { useCallback, useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Loading } from "@/components/ui/loading";
import { PageHeader } from "@/components/ui/page-header";
import { SectionHeading } from "@/components/ui/section-heading";
import { Select } from "@/components/ui/select";
import { ApiError, invites, orgs, type Member } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { failureMessage, formatDate } from "@/lib/format";

const ROLE_LABEL: Record<Member["role"], string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
};

/** Who is in the active organization, and (for owners and admins) a way to
 *  invite someone. There is no email delivery: the invite link is shown
 *  once, to be sent however the admin likes. It is the only copy (the
 *  server keeps just a hash of the token), so it cannot be shown again. */
export default function MembersPage() {
  const { activeOrgId, activeRole } = useAuth();
  const [rows, setRows] = useState<Member[]>([]);
  const [more, setMore] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Member["role"]>("member");
  const [sending, setSending] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [link, setLink] = useState<{ email: string; url: string; expires: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const canInvite = activeRole === "owner" || activeRole === "admin";

  const load = useCallback(async () => {
    if (!activeOrgId) return;
    setError(null);
    setLoading(true);
    try {
      const page = await orgs.members(activeOrgId);
      setRows(page.items);
      setMore(page.next_cursor);
    } catch (err) {
      setError(
        failureMessage(
          "Couldn't load the members.",
          err instanceof ApiError ? err.message : null,
          "Check your connection, then reload the page.",
        ),
      );
    } finally {
      setLoading(false);
    }
  }, [activeOrgId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function loadMore() {
    if (!activeOrgId || !more) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await orgs.members(activeOrgId, more);
      setRows((prev) => {
        const seen = new Set(prev.map((m) => m.user_id));
        return [...prev, ...page.items.filter((m) => !seen.has(m.user_id))];
      });
      setMore(page.next_cursor);
    } catch (err) {
      setError(
        failureMessage(
          "Couldn't load more members.",
          err instanceof ApiError ? err.message : null,
          "Try again.",
        ),
      );
    } finally {
      setLoadingMore(false);
    }
  }

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    if (!activeOrgId || !email.trim()) return;
    setSending(true);
    setInviteError(null);
    setCopied(false);
    try {
      const created = await invites.create(activeOrgId, email.trim(), role);
      setLink({
        email: created.email,
        url: created.accept_url,
        expires: new Date(created.expires_at).toLocaleString("en-GB", {
          day: "numeric",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
        }),
      });
      setEmail("");
    } catch (err) {
      setInviteError(
        failureMessage(
          "Couldn't create the invite.",
          err instanceof ApiError ? err.message : null,
          "Check the email address, then try again.",
        ),
      );
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
    <div className="mx-auto w-full max-w-240 px-4 py-8 md:px-6 lg:px-8">
      <PageHeader
        title="Members"
        description="Everyone who can open this organization's assistants. Owners and admins can invite people."
      />

      <div className="flex flex-col gap-10">
        {canInvite && (
          <section
            aria-labelledby="invite-heading"
            className="border-border bg-card flex max-w-160 flex-col gap-4 rounded-lg border p-4 sm:p-6"
          >
            <SectionHeading
              level={2}
              id="invite-heading"
              title="Invite someone"
              description="You get a link to send them yourself. Nothing is emailed."
            />
            <form onSubmit={invite} className="flex flex-wrap items-end gap-3">
              <div className="flex min-w-0 flex-[1_1_15rem] flex-col gap-1.5">
                <Label htmlFor="invite-email">Email</Label>
                <Input
                  id="invite-email"
                  type="email"
                  autoComplete="off"
                  required
                  placeholder="teammate@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div className="flex w-36 flex-col gap-1.5">
                <Label htmlFor="invite-role">Role</Label>
                <Select
                  id="invite-role"
                  value={role}
                  onChange={(e) => setRole(e.target.value as Member["role"])}
                >
                  <option value="member">Member</option>
                  <option value="admin">Admin</option>
                </Select>
              </div>
              <Button type="submit" disabled={sending}>
                {sending ? "Creating…" : "Create invite link"}
              </Button>
            </form>
            {inviteError && <Alert>{inviteError}</Alert>}
            {link && (
              <Alert tone="success" title={`Invite link for ${link.email}`}>
                <p className="mt-0.5">
                  Send it to them yourself. It&apos;s shown only once and expires{" "}
                  <span className="num">{link.expires}</span>.
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <span className="bg-muted text-small min-w-0 flex-[1_1_12rem] rounded-sm px-2 py-1.5 break-all select-all">
                    {link.url}
                  </span>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    aria-label={copied ? "Copied invite link" : "Copy invite link"}
                    onClick={() => void copy()}
                  >
                    {copied ? "Copied" : "Copy"}
                  </Button>
                  <span role="status" className="sr-only">
                    {copied ? "Invite link copied" : ""}
                  </span>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setLink(null)}>
                    Done
                  </Button>
                </div>
              </Alert>
            )}
          </section>
        )}

        <section aria-labelledby="people-heading" className="flex flex-col gap-4">
          <SectionHeading level={2} id="people-heading" title="People" />
          {error && <Alert>{error}</Alert>}
          {loading ? (
            <Loading what="members" rows={3} rowHeight={61} />
          ) : rows.length === 0 ? (
            !error && (
              <EmptyState
                title="No one here yet"
                description={
                  canInvite
                    ? "Invite people above to share this organization's assistants with them."
                    : "Ask an owner or admin of this organization to invite people."
                }
              />
            )
          ) : (
            <ul className="border-border border-t">
              {rows.map((m) => (
                <li
                  key={m.user_id}
                  className="border-border flex flex-wrap items-center gap-x-4 gap-y-1 border-b py-3"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium" title={m.name || m.email}>
                      {m.name || m.email}
                    </div>
                    {m.name && (
                      <div className="text-small text-muted-foreground truncate" title={m.email}>
                        {m.email}
                      </div>
                    )}
                  </div>
                  <span className="text-small text-muted-foreground hidden sm:inline">
                    Joined{" "}
                    <time className="num" dateTime={m.joined_at}>
                      {formatDate(m.joined_at)}
                    </time>
                  </span>
                  <Badge
                    variant="muted"
                    className="border-field-border text-foreground w-18 justify-center border bg-transparent"
                  >
                    {ROLE_LABEL[m.role] ?? m.role}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
          {more && (
            <Button
              variant="ghost"
              size="sm"
              className="self-start"
              disabled={loadingMore}
              onClick={() => void loadMore()}
            >
              {loadingMore ? "Loading…" : "Load more"}
            </Button>
          )}
        </section>
      </div>
    </div>
  );
}
