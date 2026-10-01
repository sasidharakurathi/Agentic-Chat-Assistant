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
export default function RegisterPage() {
  return (
    <Suspense fallback={<Loading what="the sign-up form" />}>
      <RegisterForm />
    </Suspense>
  );
}

function RegisterForm() {
  const next = safeNext(useSearchParams().get("next"));
  const withNext = (path: string) => (next ? `${path}?next=${encodeURIComponent(next)}` : path);
  const { register } = useAuth();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      await register(email, password, name, next);
    } catch (err) {
      setError(registerError(err));
      setPending(false);
    }
  }

  return (
    <AuthCard>
      <CardHeader>
        <h1 className="font-condensed text-h1 font-semibold">Create your account</h1>
        <CardDescription>You&apos;ll get a personal workspace to start building.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="name">Name</Label>
            <Input
              id="name"
              autoComplete="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
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
              autoComplete="new-password"
              required
              minLength={8}
              aria-describedby="password-hint"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <p id="password-hint" className="text-small text-muted-foreground">
              At least 8 characters.
            </p>
          </div>
          {error && <Alert>{error}</Alert>}
          <Button type="submit" disabled={pending}>
            {pending ? "Creating…" : "Create account"}
          </Button>
          <p className="text-muted-foreground text-sm">
            Already have an account?{" "}
            <Link
              href={withNext("/login")}
              className="text-foreground rounded-sm font-medium underline decoration-1 underline-offset-[3px] hover:decoration-2"
            >
              Sign in
            </Link>
          </p>
        </form>
      </CardContent>
    </AuthCard>
  );
}

/** What went wrong, and what to do about it. */
function registerError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "registration_by_invite") {
      return "This server creates accounts by invitation. Ask an admin of your team for an invite link, then open it and choose Create account.";
    }
    if (err.code === "email_taken") {
      return "An account with that email already exists. Sign in instead, or use another email.";
    }
    return err.message;
  }
  return "Couldn't reach the server. Check your connection, then try again.";
}
