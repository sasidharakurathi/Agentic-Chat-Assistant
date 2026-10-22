"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Loading } from "@/components/ui/loading";
import { ApiError } from "@/lib/api";
import { DeviceCodeSignIn } from "@/components/auth/DeviceCodeSignIn";
import { GoogleButton } from "@/components/auth/GoogleButton";
import { TwoFactorStep } from "@/components/auth/TwoFactorStep";
import { safeNext, useAuth } from "@/lib/auth";
import { googleError } from "@/lib/google";
import { inviteTokenFrom } from "@/lib/safe-next";

import { AuthCard } from "../auth-card";

/** `useSearchParams` reads the URL being rendered. Reading
 *  `window.location` instead gave the *previous* URL during a client-side
 *  navigation (the page renders before the address bar changes), which lost
 *  `?next=` on the way from an invite link. Next requires the Suspense. */
export default function LoginPage() {
  return (
    <Suspense fallback={<Loading what="the sign-in form" />}>
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const search = useSearchParams();
  const next = safeNext(search.get("next"));
  // Back from Google with a refusal (Phase 8.3): say why.
  const googleProblem = googleError(search.get("google"));
  const withNext = (path: string) => (next ? `${path}?next=${encodeURIComponent(next)}` : path);
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  // Two-step sign-in (Phase 8.4): the password was right, a code finishes it.
  const [challenge, setChallenge] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const needs = await login(email, password, next);
      if (needs) {
        setChallenge(needs);
        setPending(false);
      }
    } catch (err) {
      setError(signInError(err));
      setPending(false);
    }
  }

  return (
    <AuthCard>
      <CardHeader>
        <h1 className="font-condensed text-h1 font-semibold">Sign in</h1>
        <CardDescription>Welcome back. Sign in with your email and password.</CardDescription>
      </CardHeader>
      <CardContent>
        {challenge ? (
          <TwoFactorStep
            challenge={challenge}
            next={next}
            onRestart={() => {
              setChallenge(null);
              setError("That took too long. Sign in with your password again.");
            }}
          />
        ) : (
          <div className="flex flex-col gap-4">
            <form onSubmit={onSubmit} className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              {(error ?? googleProblem) && <Alert>{error ?? googleProblem}</Alert>}
              <Button type="submit" disabled={pending}>
                {pending ? "Signing in…" : "Sign in"}
              </Button>
              <GoogleButton next={next} invite={inviteTokenFrom(next)} />
            </form>
            {/* Its own form, beside the password's (forms can't nest). */}
            <DeviceCodeSignIn next={next} onChallenge={setChallenge} />
            <p className="text-muted-foreground text-sm">
              <Link
                href="/forgot-password"
                className="text-foreground rounded-sm font-medium underline decoration-1 underline-offset-[3px] hover:decoration-2"
              >
                Forgot your password?
              </Link>
            </p>
            <p className="text-muted-foreground text-sm">
              No account?{" "}
              <Link
                href={withNext("/register")}
                className="text-foreground rounded-sm font-medium underline decoration-1 underline-offset-[3px] hover:decoration-2"
              >
                Create one
              </Link>
            </p>
          </div>
        )}
      </CardContent>
    </AuthCard>
  );
}

/** What went wrong, and what to do about it. */
function signInError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "invalid_credentials") {
      return "That email and password don't match an account. Check both, then try again.";
    }
    return err.message;
  }
  // Off the office network, the server can't be reached at all (Phase 8.6).
  return "Couldn't reach the server. Are you on the office network or the company VPN? Check, then try again.";
}
