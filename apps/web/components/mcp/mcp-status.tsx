import { Badge } from "@/components/ui/badge";
import { Lamp } from "@/components/ui/lamp";
import type { McpServer } from "@/lib/api";

/** The words for how a server is reached, shared by the MCP servers tab,
 *  its preset catalog and the MCP canvas node's drawer. */
export const TRANSPORT_LABEL: Record<McpServer["transport"], string> = {
  stdio: "Local command",
  http: "Remote (HTTP)",
  sse: "Remote (SSE)",
};

/** A server's state as one status plate: lamp and word, never a raw enum.
 *  `working` shows the live "Checking" plate while a check or discovery
 *  runs; a switched-off server says so before its last result. */
export function ServerStatus({
  server,
  working = false,
}: {
  server: McpServer;
  working?: boolean;
}) {
  if (working)
    return (
      <Badge variant="muted" live>
        Checking
      </Badge>
    );
  if (!server.enabled) return <Badge variant="muted">Switched off</Badge>;
  if (server.status === "ok") return <Badge variant="success">Connected</Badge>;
  if (server.status === "error") return <Badge variant="destructive">Failed</Badge>;
  return (
    <Badge variant="muted" lamp={false}>
      <Lamp tone="off" />
      Not checked
    </Badge>
  );
}

/** Whether an MCP tool says it changes anything. Read-only is a plain muted
 *  plate (it is a property, not a "ready" state, so no green lamp); a tool
 *  that can change things gets the warning lamp, matching a database's
 *  "Can change data". A server that says neither gets "Not stated". */
export function ToolAccess({ readOnly }: { readOnly: boolean | null | undefined }) {
  if (readOnly === true) return <Badge variant="muted">Read-only</Badge>;
  if (readOnly === false) return <Badge variant="warning">Can change things</Badge>;
  return (
    <Badge variant="muted" lamp={false}>
      <Lamp tone="off" />
      Not stated
    </Badge>
  );
}
