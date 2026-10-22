/** Does a message look like it holds a password or key (Phase 9.6)?
 *
 *  A warning only, shown under the composer before sending: the server
 *  masks credentials in every message anyway (`app/security/redact.py`,
 *  `mask_typed_secrets`) and says so with the answer. These are the common
 *  shapes people paste: API keys, cloud and code-host tokens, private key
 *  blocks, and a password written out ("password: Hunter2!"). */

const SHAPES: RegExp[] = [
  /\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}/, // Anthropic, OpenAI-style
  /\bAKIA[0-9A-Z]{16}\b/, // AWS access key id
  /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}/, // GitHub tokens
  /\bgithub_pat_[A-Za-z0-9_]{30,}/,
  /\bglpat-[A-Za-z0-9_-]{20,}/, // GitLab
  /\bxox[abprs]-[A-Za-z0-9-]{10,}/, // Slack
  /\bAIza[0-9A-Za-z_-]{30,}/, // Google API key
  /\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/, // JWT
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
];

/** `password: Hunter2!`, `my pin is 4821`: the word, then a value that looks
 *  like a credential (a digit, a symbol, or a capital after a lower-case
 *  letter), as the server decides. "My password: can you reset it?" isn't. */
const TYPED =
  /\b(?:password|passwd|pwd|passcode|pin|secret|api[ _-]?key|token)(?:\s*[:=]\s*|\s+is\s+)(\S+)/gi;

function credentialLike(raw: string): boolean {
  const value = raw.replace(/[.,;:!?)"']+$/, "");
  if (value.length < 4) return false;
  if (/[^A-Za-z]/.test(value)) return true;
  return /[a-z][A-Z]/.test(value);
}

export function looksLikeSecret(text: string): boolean {
  if (SHAPES.some((re) => re.test(text))) return true;
  for (const m of text.matchAll(TYPED)) {
    if (credentialLike(m[1])) return true;
  }
  return false;
}

/** What the server puts where a credential was (`REDACTED` in
 *  `app/security/redact.py`). */
export const REDACTED = "<redacted>";

/** Could `stored` be `typed` as the server keeps it, credentials masked
 *  (Phase 9.6)? The stored text is read as a template: each `<redacted>`
 *  stands for some typed text, and everything around it must be exactly
 *  what was typed, in order. So the server's patterns needn't be copied
 *  here (and can't drift): a masked password still matches what was typed,
 *  a different message never does. Leading and trailing spaces don't count. */
export function matchesMasked(stored: string, typed: string): boolean {
  const kept = stored.trim();
  const said = typed.trim();
  if (kept === said) return true;
  const parts = kept.split(REDACTED);
  if (parts.length === 1) return false;
  const first = parts[0];
  const last = parts[parts.length - 1];
  if (!said.startsWith(first)) return false;
  // Each mask hides at least one character; the earliest place each literal
  // piece fits leaves the most room for the rest.
  let at = first.length;
  for (const piece of parts.slice(1, -1)) {
    const found = said.indexOf(piece, at + 1);
    if (found < 0) return false;
    at = found + piece.length;
  }
  return said.length - last.length >= at + 1 && said.endsWith(last);
}
