// A dummy origin to resolve against. `.invalid` is reserved (RFC 2606), so it
// can never be a real host that an attacker controls.
const SELF = "http://self.invalid";

/** A `?next=` target, only if it is a path on this site; otherwise null.
 *
 *  Anything else would turn the login page into an open redirect that lends
 *  our URL to a phishing link. String checks are not enough: the browser's
 *  URL parser strips tabs and newlines and treats `\` like `/`, so
 *  `/\t/evil.example` passed the old `startsWith("//")` test and then
 *  navigated to `//evil.example`. So the target is parsed the way the
 *  browser will parse it, and must still be on the same origin afterwards. */
export function safeNext(next: string | null | undefined): string | null {
  if (!next || !next.startsWith("/")) return null;
  let url: URL;
  try {
    url = new URL(next, SELF);
  } catch {
    return null;
  }
  if (url.origin !== SELF) return null;
  // The normalised form, so what we navigate to is exactly what was checked.
  return url.pathname + url.search + url.hash;
}

/** The invite token in a `?next=` that leads back to an invite link
 *  (`/invites/<token>`), or null.
 *
 *  A server where sign-up is by invitation asks for it when the account is
 *  created, and someone arriving from an invite link has not accepted it
 *  yet: they are here to make the account first. */
export function inviteTokenFrom(next: string | null | undefined): string | null {
  const safe = safeNext(next);
  if (!safe) return null;
  const found = /^\/invites\/([A-Za-z0-9_-]{16,200})\/?(?:[?#]|$)/.exec(safe);
  return found ? found[1] : null;
}
