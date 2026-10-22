/** The token in an emailed link (Phase 7b.6). It travels after `#`, which
 *  a browser never sends to any server: it stays out of the proxy's and the
 *  API's logs and out of the Referer. Read here, then posted. */
export function tokenFromHash(hash: string): string | null {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  const token = params.get("token")?.trim();
  return token && token.length >= 10 ? token : null;
}
