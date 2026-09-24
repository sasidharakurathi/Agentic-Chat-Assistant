"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, invites, type InvitePreview } from "@/lib/api";
import { useAuth } from "@/lib/auth";

/** The page an invite link opens (`{APP_BASE_URL}/invites/{token}`).
 *
 *  The API has handed out links to this path since Phase 0, but nothing was
 *  here. It shows what the invite is for *before* sign-in (so the invitee
 *  knows which email to use), sends a signed-out visitor through login or
 *  registration and back again, and on accept switches to the new org. */
export default function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const { ready, user, refresh, setActiveOrg, logout } = useAuth();
  const router = useRouter();
  const [invite, setInvite] = useState<InvitePreview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [accepting, setAccepting] = useState(false);

  useEffect(() => {
    invites
      .preview(token)
      .then(setInvite)
      .catch((err) =>
        setProblem(
          err instanceof ApiError && err.status === 404
            ? "This invite link isn't valid. Ask for a new one."
            : "Could not load this invite.",
        ),
      );
  }, [token]);

  async function accept() {
    setAccepting(true);
    setProblem(null);
    try {
      const joined = await invites.accept(token);
      await refresh();
      setActiveOrg(joined.org_id);
      router.push("/assistants");
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "Could not accept the invite.");
      setAccepting(false);
    }
  }

  const here = `/invites/${encodeURIComponent(token)}`;
  const wrongAccount = Boolean(
    user && invite && user.email.toLowerCase() !== invite.email.toLowerCase(),
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>{invite ? `Join ${invite.org_name}` : "Invitation"}</CardTitle>
        {invite && (
          <CardDescription>
            You have been invited as <strong>{invite.role}</strong>, for{" "}
            <strong>{invite.email}</strong>.
          </CardDescription>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        {!invite && !problem && <p className="text-muted-foreground">Loading…</p>}
        {problem && (
          <p className="text-destructive" role="alert">
            {problem}
          </p>
        )}

        {invite?.status === "accepted" && (
          <p className="text-muted-foreground">
            This invite has already been used.{" "}
            {user ? (
              <Link href="/assistants" className="text-primary hover:underline">
                Go to your assistants
              </Link>
            ) : (
              <Link href="/login" className="text-primary hover:underline">
                Sign in
              </Link>
            )}
          </p>
        )}
        {invite?.status === "expired" && (
          <p className="text-muted-foreground">
            This invite has expired. Ask whoever sent it for a new one.
          </p>
        )}

        {invite?.status === "pending" && ready && !user && (
          <div className="flex flex-col gap-2">
            <p className="text-muted-foreground">
              Sign in, or create an account, as {invite.email} to accept.
            </p>
            <Link href={`/register?next=${encodeURIComponent(here)}`} className={buttonVariants()}>
              Create an account
            </Link>
            <Link
              href={`/login?next=${encodeURIComponent(here)}`}
              className={buttonVariants({ variant: "outline" })}
            >
              I already have an account
            </Link>
          </div>
        )}

        {invite?.status === "pending" && user && wrongAccount && (
          <div className="flex flex-col gap-2">
            <p className="text-muted-foreground">
              You are signed in as {user.email}, but this invite is for {invite.email}.
            </p>
            <Button variant="outline" onClick={() => logout(here)}>
              Sign out
            </Button>
          </div>
        )}

        {invite?.status === "pending" && user && !wrongAccount && (
          <Button disabled={accepting} onClick={() => void accept()}>
            {accepting ? "Joining…" : `Join ${invite.org_name}`}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
