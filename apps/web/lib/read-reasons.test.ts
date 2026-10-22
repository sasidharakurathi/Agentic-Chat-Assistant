import { afterEach, describe, expect, it } from "vitest";

import {
  READ_REASON_HEADER,
  clearReadReasons,
  hasReadReason,
  readReasonHeaders,
  setReadReason,
} from "./read-reasons";

const cid = "8b0d5c1e-2f3a-4b5c-8d9e-0a1b2c3d4e5f";

describe("an admin's reason for opening a conversation (Phase 7b.2)", () => {
  afterEach(clearReadReasons);

  it("goes with every read of that conversation, encoded", () => {
    setReadReason(cid, "Ticket 44: जाँच");
    expect(hasReadReason(cid)).toBe(true);
    for (const path of [
      `/api/v1/conversations/${cid}`,
      `/api/v1/conversations/${cid}/messages?cursor=x`,
      `/api/v1/conversations/${cid}/turns/abc/events`,
    ]) {
      expect(readReasonHeaders(path)).toEqual({
        [READ_REASON_HEADER]: encodeURIComponent("Ticket 44: जाँच"),
      });
    }
  });

  it("goes nowhere else", () => {
    setReadReason(cid, "Ticket 44");
    expect(readReasonHeaders("/api/v1/assistants/a/conversations")).toEqual({});
    expect(readReasonHeaders(`/api/v1/conversations/${cid.replace("8", "9")}`)).toEqual({});
  });
});
