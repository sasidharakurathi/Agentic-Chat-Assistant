import { expect, type Page } from "@playwright/test";

import type { Person } from "./seed";

/** Sign in through the form, as a person would. */
export async function signIn(page: Page, who: Person): Promise<void> {
  await page.getByLabel("Email").fill(who.email);
  await page.getByLabel("Password").fill(who.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

/** Open the sign-in page and sign in. */
export async function signInFresh(page: Page, who: Person): Promise<void> {
  await page.goto("/login");
  await signIn(page, who);
  await expect(page).not.toHaveURL(/\/login/);
}

/** Open an assistant from Chat's home: its card in the Assistants list
 *  (the conversations below also name it). */
export async function openAssistant(page: Page, name: string): Promise<void> {
  await page
    .getByRole("region", { name: "Assistants" })
    .getByRole("link", { name: new RegExp(name) })
    .click();
}
