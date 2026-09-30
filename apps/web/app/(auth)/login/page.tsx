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
import { safeNext, useAuth } from "@/lib/auth";

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
  const next = safeNext(useSearchParams().get("next"));
  const withNext = (path: string) => (next ? `${path}?next=${encodeURIComponent(next)}` : path);
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      await login(email, password, next);
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
          {error && <Alert>{error}</Alert>}
          <Button type="submit" disabled={pending}>
            {pending ? "Signing in…" : "Sign in"}
          </Button>
          <p className="text-muted-foreground text-sm">
            No account?{" "}
            <Link
              href={withNext("/register")}
              className="text-foreground rounded-sm font-medium underline decoration-1 underline-offset-[3px] hover:decoration-2"
            >
              Create one
            </Link>
          </p>
        </form>
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
  return "Couldn't reach the server. Check your connection, then try again.";
}
