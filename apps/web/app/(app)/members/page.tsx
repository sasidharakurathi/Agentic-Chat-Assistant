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
import { LeaveDialog } from "@/components/members/LeaveDialog";
import { MailStatus } from "@/components/members/MailStatus";
import { NoticeSettings } from "@/components/members/NoticeSettings";
import { RetentionSection } from "@/components/members/RetentionSection";
import { OrgSettings } from "@/components/members/OrgSettings";
import { canRemove, reviewersOf } from "@/lib/assistant-lifecycle";
import { useAuth } from "@/lib/auth";
import { ROLE_WORDS } from "@/lib/roles";
import { failureMessage, formatDate } from "@/lib/format";
import type { Schemas } from "@assistant-studio/shared";

const ROLE_LABEL: Record<Member["role"], string> = ROLE_WORDS;

/** Who is in the active organization, and (for owners and admins) a way to
 *  invite someone. There is no email delivery: the invite link is shown
 *  once, to be sent however the admin likes. It is the only copy (the
 *  server keeps just a hash of the token), so it cannot be shown again. */
export default function MembersPage() {
  const { activeOrgId, activeRole, user, refresh } = useAuth();
  // Removing someone, and the invites still open (Phase 7b.4).
  const [leaving, setLeaving] = useState<Member | null>(null);
  const [pending, setPending] = useState<Schemas["InviteOut"][]>([]);
  const [peopleError, setPeopleError] = useState<string | null>(null);
  const [rows, setRows] = useState<Member[]>([]);
  const [more, setMore] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  // Most people invited chat (Phase 8.1, plan D2); a builder is a choice.
  const [role, setRole] = useState<Member["role"]>("employee");
  const [sending, setSending] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [link, setLink] = useState<{
    email: string;
    url: string;
    expires: string;
    emailed: boolean;
  } | null>(null);
  const [copied, setCopied] = useState(false);

  const canInvite = activeRole === "owner" || activeRole === "admin";
  // One reviewer means nobody checks their work (Phase 7b.3): the owner
  // then approves their own assistants, with a note.
  const soleReviewer = !loading && !more && reviewersOf(rows).length <= 1;

  const load = useCallback(async () => {
    if (!activeOrgId) return;
    setError(null);
    setLoading(true);
    try {
      const page = await orgs.members(activeOrgId);
      setRows(page.items);
      setMore(page.next_cursor);
      if (activeRole === "owner" || activeRole === "admin") {
        setPending(await orgs.invites(activeOrgId).catch(() => []));
      }
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
  }, [activeOrgId, activeRole]);

  useEffect(() => {
    void load();
  }, [load]);

  const reactivate = async (m: Member) => {
    if (!activeOrgId) return;
    setPeopleError(null);
    try {
      await orgs.reactivate(activeOrgId, m.user_id);
      await load();
    } catch (err) {
      setPeopleError(
        failureMessage(
          `Couldn't reactivate ${m.name || m.email}.`,
          err instanceof ApiError ? err.message : null,
          "Try again.",
        ),
      );
    }
  };

  const withdraw = async (inviteId: string, address: string) => {
    if (!activeOrgId) return;
    setPeopleError(null);
    try {
      await orgs.revokeInvite(activeOrgId, inviteId);
      setPending((p) => p.filter((i) => i.id !== inviteId));
    } catch (err) {
      setPeopleError(
        failureMessage(
          `Couldn't withdraw the invite for ${address}.`,
          err instanceof ApiError ? err.message : null,
          "Try again.",
        ),
      );
    }
  };

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
        emailed: created.emailed === true,
        url: created.accept_url,
        expires: new Date(created.expires_at).toLocaleString("en-GB", {
          day: "numeric",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
        }),
      });
      setEmail("");
      setPending(await orgs.invites(activeOrgId).catch(() => []));
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
        {canInvite && soleReviewer && (
          <Alert className="max-w-160">
            Only one person here can approve assistants, so their own versions go live without a
            second pair of eyes (they approve them with a note saying why). Invite another admin to
            review each other&apos;s work.
          </Alert>
        )}
        {canInvite && (
          <section
            aria-labelledby="invite-heading"
            className="border-border bg-card flex max-w-160 flex-col gap-4 rounded-lg border p-4 sm:p-6"
          >
            <SectionHeading
              level={2}
              id="invite-heading"
              title="Invite someone"
              description="They get the link by email when this server sends email, and you get it to send yourself either way."
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
                  <option value="employee">Employee (chats)</option>
                  <option value="member">Builder (chats and builds)</option>
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
                  {link.emailed
                    ? "We emailed it to them. You can send it yourself too: it"
                    : "Send it to them yourself. It"}
                  &apos;s shown only once and expires <span className="num">{link.expires}</span>.
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

        {canInvite && activeOrgId && <OrgSettings orgId={activeOrgId} onRenamed={refresh} />}
        {canInvite && activeOrgId && <NoticeSettings orgId={activeOrgId} />}
        {canInvite && activeOrgId && (
          <RetentionSection orgId={activeOrgId} owner={activeRole === "owner"} />
        )}

        {canInvite && activeOrgId && <MailStatus orgId={activeOrgId} />}

        {canInvite && pending.length > 0 && (
          <section aria-labelledby="pending-heading" className="flex max-w-160 flex-col gap-3">
            <SectionHeading
              level={2}
              id="pending-heading"
              title="Invites not yet used"
              description="A link is shown only when it's created. Lost one? Invite them again: the new link replaces the old."
            />
            <ul className="border-border border-t">
              {pending.map((i) => (
                <li
                  key={i.id}
                  className="border-border flex flex-wrap items-center gap-x-4 gap-y-1 border-b py-2.5"
                >
                  <span className="min-w-0 flex-1 truncate" title={i.email}>
                    {i.email}
                  </span>
                  <span className="text-small text-muted-foreground">
                    {ROLE_LABEL[i.role] ?? i.role},{" "}
                    {new Date(i.expires_at) < new Date() ? "expired" : "expires"}{" "}
                    <time className="num" dateTime={i.expires_at}>
                      {formatDate(i.expires_at)}
                    </time>
                  </span>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={`Withdraw the invite for ${i.email}`}
                    onClick={() => void withdraw(i.id, i.email)}
                  >
                    Withdraw
                  </Button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section aria-labelledby="people-heading" className="flex flex-col gap-4">
          <SectionHeading level={2} id="people-heading" title="People" />
          {error && <Alert>{error}</Alert>}
          {peopleError && <Alert role="alert">{peopleError}</Alert>}
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
                  {m.is_active === false && <Badge variant="warning">Removed</Badge>}
                  {canRemove({ id: user?.id, role: activeRole }, m) &&
                    (m.is_active === false ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        aria-label={`Reactivate ${m.name || m.email}`}
                        onClick={() => void reactivate(m)}
                      >
                        Reactivate
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        aria-label={`Remove ${m.name || m.email}`}
                        onClick={() => setLeaving(m)}
                      >
                        Remove
                      </Button>
                    ))}
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
      {activeOrgId && (
        <LeaveDialog
          orgId={activeOrgId}
          person={leaving}
          members={rows}
          meId={user?.id ?? null}
          onClose={() => setLeaving(null)}
          onDone={() => {
            setLeaving(null);
            void load();
          }}
        />
      )}
    </div>
  );
}
