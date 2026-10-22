"use client";

import { useCallback, useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { SectionHeading } from "@/components/ui/section-heading";
import { AwaySection } from "@/components/account/AwaySection";
import { DeviceCodeSection } from "@/components/account/DeviceCodeSection";
import { GoogleConnection } from "@/components/account/GoogleConnection";
import { TwoFactorSection } from "@/components/account/TwoFactorSection";
import { ApiError, auth, tokenStore, type Me } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function said(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/** Your account (Phase 7b.6): change your password, confirm your email. */
export default function AccountPage() {
  const { user, refresh } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [pwNote, setPwNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [pwBusy, setPwBusy] = useState(false);
  const [mailNote, setMailNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [mailBusy, setMailBusy] = useState(false);
  // How this account signs in, and whether Google is set up (Phase 8.3).
  const [signIn, setSignIn] = useState<Me["sign_in"]>(null);
  const [googleOn, setGoogleOn] = useState(false);
  const [googleOutcome] = useState<string | null>(() =>
    typeof window === "undefined"
      ? null
      : new URLSearchParams(window.location.search).get("google"),
  );
  const loadSignIn = useCallback(async () => {
    try {
      const [me, providers] = await Promise.all([auth.me(), auth.providers()]);
      setSignIn(me.sign_in ?? null);
      setGoogleOn(providers.google);
    } catch {
      setSignIn(null);
    }
  }, []);
  useEffect(() => {
    void loadSignIn();
    if (googleOutcome) window.history.replaceState(null, "", "/account");
  }, [loadSignIn, googleOutcome]);

  if (!user) return null;
  const hasPassword = signIn?.password ?? true;
  const verified = Boolean(user.email_verified_at);

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    if (next !== again) {
      setPwNote({ ok: false, text: "The two new passwords don't match." });
      return;
    }
    setPwBusy(true);
    setPwNote(null);
    try {
      // Every session ends; this one carries on with the fresh pair.
      tokenStore.set(await auth.changePassword(current, next));
      setCurrent("");
      setNext("");
      setAgain("");
      setPwNote({
        ok: true,
        text: "Your password is changed. Every other device was signed out.",
      });
    } catch (err) {
      setPwNote({ ok: false, text: said(err, "Couldn't reach the server. Try again.") });
    } finally {
      setPwBusy(false);
    }
  }

  async function sendLink() {
    setMailBusy(true);
    setMailNote(null);
    try {
      setMailNote({ ok: true, text: (await auth.sendVerification()).message });
      await refresh();
    } catch (err) {
      setMailNote({ ok: false, text: said(err, "Couldn't reach the server. Try again.") });
    } finally {
      setMailBusy(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-160 px-4 py-8 md:px-6 lg:px-8">
      <PageHeader title="Your account" description={user.name || user.email} />
      <div className="flex flex-col gap-10">
        <section aria-labelledby="email-heading" className="flex flex-col gap-3">
          <SectionHeading level={2} id="email-heading" title="Email" />
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm">{user.email}</span>
            <Badge variant={verified ? "success" : "warning"}>
              {verified ? "Confirmed" : "Not confirmed"}
            </Badge>
          </div>
          {!verified && (
            <div className="flex flex-col gap-2">
              <p className="text-small text-muted-foreground">
                Confirm that this address reaches you: we email a link to open.
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="self-start"
                disabled={mailBusy}
                onClick={() => void sendLink()}
              >
                {mailBusy ? "Sending…" : "Email me a link"}
              </Button>
            </div>
          )}
          {mailNote && <Alert tone={mailNote.ok ? "success" : undefined}>{mailNote.text}</Alert>}
        </section>

        <GoogleConnection
          connected={signIn?.google ?? null}
          hasPassword={hasPassword}
          available={googleOn}
          outcome={googleOutcome}
          onChange={() => void loadSignIn()}
        />

        <TwoFactorSection onChange={() => void refresh()} />

        <DeviceCodeSection />

        <AwaySection />

        {!hasPassword ? (
          <section aria-labelledby="password-heading" className="flex flex-col gap-3">
            <SectionHeading
              level={2}
              id="password-heading"
              title="Password"
              description="This account signs in with Google: it has no password."
            />
          </section>
        ) : (
          <section aria-labelledby="password-heading" className="flex flex-col gap-3">
            <SectionHeading
              level={2}
              id="password-heading"
              title="Password"
              description="Changing it signs out every other device."
            />
            <form onSubmit={changePassword} className="flex max-w-96 flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="current">Current password</Label>
                <Input
                  id="current"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={current}
                  onChange={(e) => setCurrent(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="new">New password</Label>
                <Input
                  id="new"
                  type="password"
                  autoComplete="new-password"
                  minLength={8}
                  required
                  value={next}
                  onChange={(e) => setNext(e.target.value)}
                />
                <p className="text-small text-muted-foreground">At least 8 characters.</p>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="again">New password, again</Label>
                <Input
                  id="again"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={again}
                  onChange={(e) => setAgain(e.target.value)}
                />
              </div>
              {pwNote && (
                <Alert
                  tone={pwNote.ok ? "success" : undefined}
                  role={pwNote.ok ? undefined : "alert"}
                >
                  {pwNote.text}
                </Alert>
              )}
              <Button type="submit" className="self-start" disabled={pwBusy}>
                {pwBusy ? "Changing…" : "Change password"}
              </Button>
            </form>
          </section>
        )}
      </div>
    </div>
  );
}
