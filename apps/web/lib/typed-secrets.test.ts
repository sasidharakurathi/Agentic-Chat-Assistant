import { describe, expect, it } from "vitest";

import { REDACTED, looksLikeSecret, matchesMasked } from "./typed-secrets";

describe("looksLikeSecret", () => {
  it("spots the shapes people paste", () => {
    expect(looksLikeSecret(`my key is sk-ant-${"x".repeat(30)}`)).toBe(true);
    expect(looksLikeSecret("AKIAABCDEFGHIJKLMNOP is the id")).toBe(true);
    expect(looksLikeSecret(`token ghp_${"a".repeat(36)}`)).toBe(true);
    expect(looksLikeSecret("-----BEGIN RSA PRIVATE KEY-----\nabc")).toBe(true);
  });

  it("spots a password written out", () => {
    expect(looksLikeSecret("password: Hunter2!")).toBe(true);
    expect(looksLikeSecret("my pin is 4821")).toBe(true);
  });

  it("leaves ordinary questions alone", () => {
    expect(looksLikeSecret("I forgot my password: can you reset it?")).toBe(false);
    expect(looksLikeSecret("the token is invalid")).toBe(false);
    expect(looksLikeSecret("How do I reset my password?")).toBe(false);
  });
});

describe("matchesMasked", () => {
  const key = `sk-ant-${"x".repeat(30)}`;

  it("matches what was typed against the text the server kept", () => {
    expect(matchesMasked("hello there", " hello there ")).toBe(true);
    expect(matchesMasked(`my key is ${REDACTED}`, `my key is ${key}`)).toBe(true);
    expect(matchesMasked(`${REDACTED} is my key`, `${key} is my key`)).toBe(true);
    expect(
      matchesMasked(
        `key ${REDACTED} and password: ${REDACTED}!`,
        `key ${key} and password: Hunter2!!`,
      ),
    ).toBe(true);
    // A private key block spans lines.
    expect(
      matchesMasked(
        `here: ${REDACTED} thanks`,
        "here: -----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY----- thanks",
      ),
    ).toBe(true);
    // Typed "<redacted>" itself is left as it is by the server.
    expect(matchesMasked(`the word ${REDACTED}`, `the word ${REDACTED}`)).toBe(true);
  });

  it("never takes a different message for the one kept", () => {
    expect(matchesMasked("hello there", "hello")).toBe(false);
    expect(matchesMasked(`my key is ${REDACTED}`, "my key is")).toBe(false);
    expect(matchesMasked(`my key is ${REDACTED}`, `your key is ${key}`)).toBe(false);
    expect(matchesMasked(`pin ${REDACTED} then ${REDACTED} ok`, "pin 1234 then ok")).toBe(false);
    expect(matchesMasked(`a ${REDACTED} b ${REDACTED} c`, "a x c b y")).toBe(false);
    expect(matchesMasked(`password: ${REDACTED}!`, "password: Hunter2? ")).toBe(false);
  });
});
