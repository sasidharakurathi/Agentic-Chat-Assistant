/** What the browser tests start from (Phase 8.11), made through the API
 *  as a person would: an owner, an organisation, two employees invited into
 *  it, and an assistant approved for Chat. The passwords are made fresh for
 *  each run and kept in `e2e/.state.json` (ignored by git) for the tests. */

import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const API = "http://127.0.0.1:8020/api/v1";
export const STATE_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), ".state.json");

export type Person = { email: string; password: string; userId: string };
export type Seed = {
  orgId: string;
  assistantId: string;
  assistantName: string;
  owner: Person;
  /** Hasn't accepted the privacy notice: the notice test's. */
  fresh: Person;
  /** Has accepted it; owns `conversationId`. */
  regular: Person;
  conversationId: string;
  conversationTitle: string;
};

export function readSeed(): Seed {
  return JSON.parse(readFileSync(STATE_FILE, "utf-8")) as Seed;
}

async function call<T>(
  method: string,
  url: string,
  body?: unknown,
  token?: string,
  orgId?: string,
): Promise<T> {
  const res = await fetch(`${API}${url}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(orgId ? { "X-Org-Id": orgId } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`[e2e seed] ${method} ${url}: ${res.status} ${text.slice(0, 300)}`);
  return (text ? JSON.parse(text) : null) as T;
}

async function register(email: string, inviteToken?: string): Promise<Person & { token: string }> {
  const password = randomBytes(18).toString("base64url");
  const pair = await call<{ access_token: string }>("POST", "/auth/register", {
    email,
    password,
    name: email.split("@")[0],
    invite_token: inviteToken ?? null,
  });
  const me = await call<{ user: { id: string } }>("GET", "/auth/me", undefined, pair.access_token);
  return { email, password, userId: me.user.id, token: pair.access_token };
}

export async function seed(): Promise<Seed> {
  const owner = await register("owner@example.com");
  const org = await call<{ id: string }>("POST", "/orgs", { name: "Acme" }, owner.token);
  // As an employee joins: signing up through the invite, which joins that
  // organisation only (Phase 7b.4).
  const join = async (email: string) => {
    const invite = await call<{ accept_url: string }>(
      "POST",
      `/orgs/${org.id}/invites`,
      { email, role: "employee" },
      owner.token,
    );
    return register(email, invite.accept_url.split("/").pop() as string);
  };
  const fresh = await join("pat@example.com");
  const regular = await join("sam@example.com");

  const assistantName = "IT Helpdesk";
  const assistant = await call<{ id: string }>(
    "POST",
    "/assistants",
    { name: assistantName },
    owner.token,
    org.id,
  );
  const version = await call<{ version_number: number }>(
    "POST",
    `/assistants/${assistant.id}/versions`,
    { note: "First version" },
    owner.token,
    org.id,
  );
  await call(
    "POST",
    `/assistants/${assistant.id}/versions/${version.version_number}/approve`,
    { note: "Approved for the browser tests" },
    owner.token,
    org.id,
  );

  // The regular employee has read the notice, and has a conversation to
  // link to.
  await call("POST", "/chat/notice:accept", { version: 1 }, regular.token, org.id);
  const conversation = await call<{ id: string }>(
    "POST",
    `/assistants/${assistant.id}/conversations`,
    {},
    regular.token,
    org.id,
  );
  const conversationTitle = "Laptop won't start";
  await call(
    "PATCH",
    `/conversations/${conversation.id}`,
    { title: conversationTitle },
    regular.token,
    org.id,
  );

  const strip = ({ email, password, userId }: Person & { token: string }): Person => ({
    email,
    password,
    userId,
  });
  return {
    orgId: org.id,
    assistantId: assistant.id,
    assistantName,
    owner: strip(owner),
    fresh: strip(fresh),
    regular: strip(regular),
    conversationId: conversation.id,
    conversationTitle,
  };
}
