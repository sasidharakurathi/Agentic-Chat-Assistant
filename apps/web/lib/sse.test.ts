import { describe, expect, it } from "vitest";

import { parseSseFrame } from "./api";

describe("parseSseFrame", () => {
  it("reads the event, and its id when the frame has one", () => {
    expect(parseSseFrame('id: 1712-0\ndata: {"type":"token","text":"Hi"}')).toEqual({
      id: "1712-0",
      event: { type: "token", text: "Hi" },
    });
    // The id may come after the data: the order of fields is free.
    expect(parseSseFrame('data: {"type":"done","message_id":"m","run_id":"r"}\nid: 9')).toEqual({
      id: "9",
      event: { type: "done", message_id: "m", run_id: "r" },
    });
  });

  it("reads a frame with no id, as sending a message used to stream them", () => {
    expect(parseSseFrame('data: {"type":"token","text":"x"}')).toEqual({
      id: null,
      event: { type: "token", text: "x" },
    });
  });

  it("joins a data field split over several lines, and tolerates CRLF", () => {
    expect(parseSseFrame('data: {"type":"token",\r\ndata: "text":"a"}\r')).toEqual({
      id: null,
      event: { type: "token", text: "a" },
    });
  });

  it("skips a frame with no data: a comment or a keep-alive", () => {
    expect(parseSseFrame(": keep-alive")).toBeNull();
    expect(parseSseFrame("id: 3")).toBeNull();
  });
});
