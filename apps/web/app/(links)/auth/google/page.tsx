"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { AuthCard } from "@/app/(auth)/auth-card";
import { Alert } from "@/components/ui/alert";
import { CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { TwoFactorStep } from "@/components/auth/TwoFactorStep";
import { Loading } from "@/components/ui/loading";
import { ApiError, auth } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { parseHandoff } from "@/lib/google";

/** Back from Google (Phase 8.3): the one-minute code after `#` is traded
 *  for this browser's session tokens, then on to where the person was
 *  going. Only once: a reload finds the code spent. */
export default function GoogleHandoffPage() {
  const { adopt } = useAuth();
  const [failed, setFailed] = useState<string | null>(null);
  const [challenge, setChallenge] = useState<string | null>(null);
  const [nextPage, setNextPage] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const { code, next } = parseHandoff(window.location.hash);
    // The code leaves the address bar and the history at once.
    window.history.replaceState(null, "", "/auth/google");
    if (!code) {
      setFailed("This sign-in link is incomplete. Sign in again.");
      return;
    }
    setNextPage(next);
    auth
      .googleExchange(code)
      .then((pair) => adopt(pair, next))
      .then((needs) => {
        if (needs) setChallenge(needs);
      })
      .catch((err) =>
        setFailed(
          err instanceof ApiError
            ? "This sign-in has expired or was already used. Sign in again."
            : "Couldn't reach the server. Check your connection, then sign in again.",
        ),
      );
  }, [adopt]);

  return (
    <AuthCard>
      <CardHeader>
        <h1 className="font-condensed text-h1 font-semibold">Signing you in</h1>
        <CardDescription>With your Google account.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {challenge ? (
          <TwoFactorStep
            challenge={challenge}
            next={nextPage}
            onRestart={() => {
              setChallenge(null);
              setFailed("That took too long. Sign in again.");
            }}
          />
        ) : failed ? (
          <>
            <Alert>{failed}</Alert>
            <Link
              href="/login"
              className="text-foreground rounded-sm font-medium underline decoration-1 underline-offset-[3px] hover:decoration-2"
            >
              Back to sign in
            </Link>
          </>
        ) : (
          <Loading what="your account" rows={1} />
        )}
      </CardContent>
    </AuthCard>
  );
}
