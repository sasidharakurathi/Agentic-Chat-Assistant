/** What changed between two versions beyond their settings (Phase 7b.1):
 *  their knowledge, database connections and MCP tools, from the manifests
 *  recorded when each was submitted (HTTP connections since Phase 9.3). A reviewer reads these beside the
 *  config diff: a new copy of a policy document, or a connection that now
 *  allows writes, changes what employees get as much as a new prompt. */

type Source = { id: string; name: string };
type Doc = { id: string; source_id: string };
type Fingerprinted = { id: string; name: string; fingerprint: string };
type Server = Fingerprinted & { tools?: Record<string, string> };

export type Manifest = {
  knowledge?: { sources?: Source[]; documents?: Doc[] };
  databases?: Fingerprinted[];
  mcp_servers?: Server[];
  http_connections?: Fingerprinted[];
};

export type ManifestChange = {
  kind: "knowledge" | "database" | "mcp_server" | "mcp_tool" | "http_connection";
  change: "added" | "removed" | "changed";
  /** Says what changed, for the reviewer. */
  text: string;
};

function byId<T extends { id: string }>(items: T[] | undefined): Map<string, T> {
  return new Map((items ?? []).map((i) => [i.id, i]));
}

function docsOf(m: Manifest | null | undefined, sourceId: string): string[] {
  return (m?.knowledge?.documents ?? [])
    .filter((d) => d.source_id === sourceId)
    .map((d) => d.id)
    .sort();
}

/** The changes from `from` to `to`. A version from before manifests were
 *  recorded has none: nothing can be said about it, so nothing is listed. */
export function compareManifests(
  from: Manifest | null | undefined,
  to: Manifest | null | undefined,
): ManifestChange[] {
  if (!from || !to) return [];
  const out: ManifestChange[] = [];

  const a = byId(from.knowledge?.sources);
  const b = byId(to.knowledge?.sources);
  for (const [id, s] of b) {
    if (!a.has(id)) {
      out.push({ kind: "knowledge", change: "added", text: `Knowledge source "${s.name}" added` });
    } else if (docsOf(from, id).join() !== docsOf(to, id).join()) {
      out.push({
        kind: "knowledge",
        change: "changed",
        text: `Knowledge source "${s.name}" has new content`,
      });
    }
  }
  for (const [id, s] of a) {
    if (!b.has(id)) {
      out.push({
        kind: "knowledge",
        change: "removed",
        text: `Knowledge source "${s.name}" removed`,
      });
    }
  }

  const da = byId(from.databases);
  const db = byId(to.databases);
  for (const [id, c] of db) {
    const before = da.get(id);
    if (!before) {
      out.push({
        kind: "database",
        change: "added",
        text: `Database connection "${c.name}" added`,
      });
    } else if (before.fingerprint !== c.fingerprint) {
      out.push({
        kind: "database",
        change: "changed",
        text: `Database connection "${c.name}" changed (where it connects, as whom, or what it may do)`,
      });
    }
  }
  for (const [id, c] of da) {
    if (!db.has(id)) {
      out.push({
        kind: "database",
        change: "removed",
        text: `Database connection "${c.name}" removed`,
      });
    }
  }

  const ma = byId(from.mcp_servers);
  const mb = byId(to.mcp_servers);
  for (const [id, s] of mb) {
    const before = ma.get(id);
    if (!before) {
      out.push({ kind: "mcp_server", change: "added", text: `MCP server "${s.name}" added` });
      continue;
    }
    if (before.fingerprint !== s.fingerprint) {
      out.push({
        kind: "mcp_server",
        change: "changed",
        text: `MCP server "${s.name}" changed (its address, command or credentials)`,
      });
    }
    const ta = before.tools ?? {};
    const tb = s.tools ?? {};
    for (const [tool, fp] of Object.entries(tb)) {
      if (!(tool in ta)) {
        out.push({
          kind: "mcp_tool",
          change: "added",
          text: `Tool "${tool}" on "${s.name}" added`,
        });
      } else if (ta[tool] !== fp) {
        out.push({
          kind: "mcp_tool",
          change: "changed",
          text: `Tool "${tool}" on "${s.name}" changed (its description, inputs or read-only hint)`,
        });
      }
    }
    for (const tool of Object.keys(ta)) {
      if (!(tool in tb)) {
        out.push({
          kind: "mcp_tool",
          change: "removed",
          text: `Tool "${tool}" on "${s.name}" removed`,
        });
      }
    }
  }
  for (const [id, s] of ma) {
    if (!mb.has(id)) {
      out.push({ kind: "mcp_server", change: "removed", text: `MCP server "${s.name}" removed` });
    }
  }

  const ha = byId(from.http_connections);
  const hb = byId(to.http_connections);
  for (const [id, c] of hb) {
    const before = ha.get(id);
    if (!before) {
      out.push({
        kind: "http_connection",
        change: "added",
        text: `HTTP connection "${c.name}" added`,
      });
    } else if (before.fingerprint !== c.fingerprint) {
      out.push({
        kind: "http_connection",
        change: "changed",
        text: `HTTP connection "${c.name}" changed (its address, credentials, or whether it says who is asking)`,
      });
    }
  }
  for (const [id, c] of ha) {
    if (!hb.has(id)) {
      out.push({
        kind: "http_connection",
        change: "removed",
        text: `HTTP connection "${c.name}" removed`,
      });
    }
  }
  return out;
}
