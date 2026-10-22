"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AuthCard } from "@/app/(auth)/auth-card";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { ApiError, auth } from "@/lib/api";
import { tokenFromHash } from "@/lib/link-token";

const LINK =
  "text-foreground rounded-sm font-medium underline decoration-1 underline-offset-[3px] hover:decoration-2";

/** Confirming an address from an emailed link (Phase 7b.6). A button, not
 *  automatic: a mail scanner opening the page confirms nothing. */
export default function VerifyEmailPage() {
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const [state, setState] = useState<"ready" | "done" | "failed">("ready");
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    setToken(tokenFromHash(window.location.hash));
  }, []);

  async function confirm() {
    if (!token) return;
    setPending(true);
    try {
      setMessage((await auth.verifyEmail(token)).message);
      setState("done");
      window.history.replaceState(null, "", "/verify-email");
    } catch (err) {
      setMessage(
        err instanceof ApiError
          ? err.message
          : "Couldn't reach the server. Check your connection, then try again.",
      );
      setState("failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthCard>
      <CardHeader>
        <h1 className="font-condensed text-h1 font-semibold">Confirm your email</h1>
        <CardDescription>So Assistant Studio knows this address reaches you.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        {token === null && (
          <Alert role="alert">
            This link is incomplete. Open it again from the email, or send a new one from your
            account page.
          </Alert>
        )}
        {state === "done" && (
          <>
            <Alert tone="success">{message}</Alert>
            <Link href="/account" className={LINK}>
              Go to your account
            </Link>
          </>
        )}
        {state === "failed" && (
          <Alert role="alert">
            {message}{" "}
            <Link href="/account" className={LINK}>
              Send a new link
            </Link>
          </Alert>
        )}
        {token && state === "ready" && (
          <Button onClick={() => void confirm()} disabled={pending}>
            {pending ? "Confirming…" : "Confirm my email"}
          </Button>
        )}
      </CardContent>
    </AuthCard>
  );
}
