"use client";

import Link from "next/link";
import { useState } from "react";

import { AuthCard } from "@/app/(auth)/auth-card";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, auth } from "@/lib/api";

const LINK =
  "text-foreground rounded-sm font-medium underline decoration-1 underline-offset-[3px] hover:decoration-2";

/** Asking for a reset link (Phase 7b.6). The answer is the same whether or
 *  not an account has the address: the form tells nobody who has one. */
export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      setDone((await auth.requestReset(email.trim())).message);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Couldn't reach the server. Check your connection, then try again.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthCard>
      <CardHeader>
        <h1 className="font-condensed text-h1 font-semibold">Reset your password</h1>
        <CardDescription>
          We&apos;ll email a link to choose a new one. If this server doesn&apos;t send email, ask
          an admin.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {done ? (
          <div className="flex flex-col gap-3 text-sm">
            <Alert tone="success">{done}</Alert>
            <p className="text-muted-foreground">
              The link works once, for an hour.{" "}
              <Link href="/login" className={LINK}>
                Back to sign in
              </Link>
            </p>
          </div>
        ) : (
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
            {error && <Alert role="alert">{error}</Alert>}
            <Button type="submit" disabled={pending}>
              {pending ? "Sending…" : "Email me a link"}
            </Button>
            <p className="text-muted-foreground text-sm">
              <Link href="/login" className={LINK}>
                Back to sign in
              </Link>
            </p>
          </form>
        )}
      </CardContent>
    </AuthCard>
  );
}
