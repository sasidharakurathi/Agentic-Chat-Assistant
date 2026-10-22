"use client";

import qrcode from "qrcode-generator";
import { useCallback, useEffect, useId, useMemo, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SectionHeading } from "@/components/ui/section-heading";
import { ApiError, tokenStore, twoFactor, type TwoFactorStatus } from "@/lib/api";

function said(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return "Too many tries. Wait a few minutes, then try again.";
    return err.message;
  }
  return "Couldn't reach the server. Try again.";
}

/** The QR code an authenticator app scans, as an SVG data address. */
function qrSvg(uri: string): string {
  const qr = qrcode(0, "M");
  qr.addData(uri);
  qr.make();
  return qr.createDataURL(4, 2);
}

/** Two-step sign-in on Your account (Phase 8.4): set it up, confirm a
 *  session with a code, new recovery codes, turn it off. */
export function TwoFactorSection({ onChange }: { onChange: () => void }) {
  const [status, setStatus] = useState<TwoFactorStatus | null>(null);
  const [setup, setSetup] = useState<{ secret: string; uri: string } | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const id = useId();

  const load = useCallback(async () => {
    try {
      setStatus(await twoFactor.status());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const qr = useMemo(() => (setup ? qrSvg(setup.uri) : null), [setup]);

  if (!status) return null;

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setNote(null);
    try {
      await work();
      setCode("");
      await load();
      onChange();
    } catch (err) {
      setNote({ ok: false, text: said(err) });
    } finally {
      setBusy(false);
    }
  };

  const begin = () =>
    run(async () => {
      const s = await twoFactor.setup();
      setSetup({ secret: s.secret, uri: s.otpauth_uri });
    });
  const confirm = () =>
    run(async () => {
      const done = await twoFactor.confirm(code);
      // A fresh session that has passed it: admin powers open here.
      tokenStore.set(done.tokens);
      setSetup(null);
      setCodes(done.recovery_codes);
      setNote({ ok: true, text: "Two-step sign-in is on." });
    });
  const verify = () =>
    run(async () => {
      tokenStore.set(await twoFactor.verify(code));
      setNote({ ok: true, text: "Confirmed: admin features are open in this session." });
    });
  const regenerate = () =>
    run(async () => {
      setCodes((await twoFactor.recoveryCodes(code)).recovery_codes);
      setNote({ ok: true, text: "New recovery codes: the old ones no longer work." });
    });
  const disable = () =>
    run(async () => {
      setNote({ ok: true, text: (await twoFactor.disable(code)).message });
      setCodes(null);
    });

  const codeField = (label: string) => (
    <div className="flex max-w-60 flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        inputMode="numeric"
        autoComplete="one-time-code"
        value={code}
        onChange={(e) => setCode(e.target.value)}
      />
    </div>
  );

  return (
    <section aria-labelledby="two-factor-heading" className="flex flex-col gap-3">
      <SectionHeading
        level={2}
        id="two-factor-heading"
        title="Two-step sign-in"
        description={
          status.enabled
            ? `On: signing in asks for a code from your authenticator app. ${status.recovery_codes_left} recovery codes left.`
            : status.required
              ? "Needed for admin features: without it, you can build and chat but not act as an admin."
              : "A code from an authenticator app after your password, so a stolen password isn't enough."
        }
      />

      {codes && (
        <div className="border-border flex flex-col gap-2 rounded-md border p-4">
          <p className="font-medium">Your recovery codes</p>
          <p className="text-small text-muted-foreground">
            Each works once, if you lose your phone. Keep them somewhere safe, away from it. They
            won&apos;t be shown again.
          </p>
          <ul className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-sm sm:grid-cols-5">
            {codes.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      )}

      {!status.enabled && !setup && (
        <Button className="self-start" disabled={busy} onClick={() => void begin()}>
          Set up two-step sign-in
        </Button>
      )}

      {!status.enabled && setup && qr && (
        <div className="flex flex-col gap-3">
          <p>
            Scan this with your authenticator app (Google Authenticator, Microsoft Authenticator,
            1Password…), then type the code it shows.
          </p>
          {/* eslint-disable-next-line @next/next/no-img-element -- a data address, nothing to optimise */}
          <img
            src={qr}
            alt="QR code for your authenticator app"
            className="border-border size-44 rounded-md border bg-white p-1"
          />
          <p className="text-small text-muted-foreground">
            Can&apos;t scan? Type this key into the app:{" "}
            <span className="bg-muted rounded-sm px-1 font-mono break-all select-all">
              {setup.secret}
            </span>
          </p>
          {codeField("Code from the app")}
          <Button className="self-start" disabled={busy || !code} onClick={() => void confirm()}>
            {busy ? "Checking…" : "Turn it on"}
          </Button>
        </div>
      )}

      {status.enabled && (
        <div className="flex flex-col gap-3">
          {codeField("Code from the app, or a recovery code")}
          <div className="flex flex-wrap gap-2">
            {status.required && !status.session_verified && (
              <Button disabled={busy || !code} onClick={() => void verify()}>
                Confirm it&apos;s me
              </Button>
            )}
            <Button variant="outline" disabled={busy || !code} onClick={() => void regenerate()}>
              New recovery codes
            </Button>
            <Button variant="outline" disabled={busy || !code} onClick={() => void disable()}>
              Turn off
            </Button>
          </div>
        </div>
      )}

      {note && <Alert tone={note.ok ? "success" : undefined}>{note.text}</Alert>}
    </section>
  );
}
