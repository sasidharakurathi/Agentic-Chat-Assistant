import { expect, test } from "@playwright/test";

import { openAssistant, signIn, signInFresh } from "./helpers";
import { readSeed, type Seed } from "./seed";

/** The employee flows (Phase 8.11), in a browser, on the throwaway stack:
 *  the checks that only show in a page. An employee lands in Chat and never
 *  sees the Studio; the privacy notice comes before the first message; a
 *  link survives signing in; a dropped connection picks the answer up; a
 *  phone can sign in and chat; removing a conversation says what it does
 *  and can be undone. In order: they share one seed. */

// Read once the global setup has written it, not when the file is loaded.
let seed: Seed;
test.beforeAll(() => {
  seed = readSeed();
});

test("an employee lands in Chat, and the Studio's pages send them back", async ({ page }) => {
  await signInFresh(page, seed.regular);
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByRole("link", { name: /Your account/ })).toBeVisible();
  // The Studio's rail isn't there at all.
  await expect(page.getByRole("link", { name: "Assistants", exact: true })).toHaveCount(0);
  for (const studio of ["/assistants", "/members", `/assistants/${seed.assistantId}/build`]) {
    await page.goto(studio);
    await expect(page).toHaveURL(/\/chat$/);
  }
});

test("the privacy notice comes before the first message, then the assistant answers", async ({
  page,
}) => {
  await signInFresh(page, seed.fresh);
  await expect(page.getByRole("heading", { name: "How Chat uses your data" })).toBeVisible();
  await expect(page.getByText("Admins and the owner can open any conversation")).toBeVisible();
  await page.getByRole("button", { name: "I've read it: continue" }).click();

  await openAssistant(page, seed.assistantName);
  await page.getByRole("button", { name: "Start a conversation" }).click();
  await page.getByLabel(`Message ${seed.assistantName}`).fill("My VPN keeps dropping");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("you said: My VPN keeps dropping")).toBeVisible();

  // A thumbs, and "Did this solve it?".
  const good = page.getByRole("button", { name: "Good answer" });
  await good.click();
  await expect(good).toHaveAttribute("aria-pressed", "true");
  await page
    .getByRole("group", { name: "Did this solve it?" })
    .getByRole("button", {
      name: "Yes",
    })
    .click();
  await expect(page.getByText("You said this solved it.")).toBeVisible();
});

test("a link to a conversation survives signing in", async ({ page }) => {
  await page.goto(`/chat/c/${seed.conversationId}`);
  await expect(page).toHaveURL(/\/login\?next=/);
  await signIn(page, seed.regular);
  await expect(page).toHaveURL(new RegExp(`conversation=${seed.conversationId}`));
  await expect(page.getByRole("heading", { name: seed.conversationTitle })).toBeVisible();
});

test("a dropped connection picks the answer up where it left off", async ({ page }) => {
  await signInFresh(page, seed.regular);
  await openAssistant(page, seed.assistantName);
  await page.getByRole("button", { name: "New chat" }).first().click();
  // A new chat swaps in a fresh thread: type once it has, and still holds.
  await expect(page.getByRole("heading", { level: 1, name: "New conversation" })).toBeVisible();
  const box = page.getByLabel(`Message ${seed.assistantName}`);
  const send = page.getByRole("button", { name: "Send", exact: true });
  await expect(async () => {
    await box.fill("Wi-Fi drops at my desk");
    await expect(box).toHaveValue("Wi-Fi drops at my desk", { timeout: 1_000 });
    await expect(send).toBeEnabled({ timeout: 1_000 });
  }).toPass();

  // The connection drops partway: the page gets the first few events of
  // the answer and then the stream ends, with no "done". (The browser's
  // offline switch doesn't cut a stream that is already open.) The turn
  // carries on on the server.
  await page.route(
    "**/api/v1/conversations/*/messages",
    async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      const response = await route.fetch();
      const frames = (await response.text()).split("\n\n").filter(Boolean);
      expect(frames.length).toBeGreaterThan(3);
      await route.fulfill({ response, body: `${frames.slice(0, 3).join("\n\n")}\n\n` });
    },
    { times: 1 },
  );
  await send.click();

  // The page notices, finds the turn, and watches it again from the last
  // event it saw.
  await expect(page.getByText("Reconnecting…")).toBeVisible();
  // The answer arrives once: nothing missed, nothing twice.
  await expect(page.getByText("you said: Wi-Fi drops at my desk")).toBeVisible();
  await expect(page.getByText("(fake driver)")).toHaveCount(1);
  await expect(page.getByText(/Lost the connection/)).toHaveCount(0);
});

test("on a phone, an employee signs in and chats", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/login");
  // The form, not "sign in from a computer".
  await expect(page.getByLabel("Email")).toBeVisible();
  await signIn(page, seed.regular);
  await expect(page).toHaveURL(/\/chat$/);
  await openAssistant(page, seed.assistantName);
  // Their latest conversation opens, ready to type in.
  await expect(page.getByLabel(`Message ${seed.assistantName}`)).toBeVisible();
  await expect(page.getByRole("button", { name: "Conversations" })).toBeVisible();
});

test("removing a conversation says what it does, and it can be put back", async ({ page }) => {
  await signInFresh(page, seed.regular);
  await page.goto(`/assistants/${seed.assistantId}/chat?conversation=${seed.conversationId}`);
  const row = page.getByRole("button", { name: seed.conversationTitle, exact: true });
  await row.hover();
  await page
    .getByRole("button", { name: `Remove ${seed.conversationTitle} from your history` })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("admins can still open it");
  await dialog.getByRole("button", { name: "Remove", exact: true }).click();

  await page.goto("/chat");
  await page.getByRole("button", { name: "Removed", exact: true }).click();
  await expect(page.getByText(seed.conversationTitle)).toBeVisible();
  await page.getByRole("button", { name: "Put back" }).click();
  await expect(page.getByText(seed.conversationTitle)).toHaveCount(0);
  await page.getByRole("button", { name: "In your history", exact: true }).click();
  await expect(page.getByText(seed.conversationTitle)).toBeVisible();
});

test("Privacy & help shows the notice and who opened your conversations", async ({ page }) => {
  await signInFresh(page, seed.regular);
  await page.getByRole("link", { name: /Privacy/ }).click();
  await expect(page.getByRole("heading", { name: "Privacy & help" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Access to your conversations" })).toBeVisible();
  await expect(page.getByText("Nobody has opened any of your conversations.")).toBeVisible();
});
