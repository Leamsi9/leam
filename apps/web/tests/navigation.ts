import { expect, type Page } from "@playwright/test";
export async function navigate(page: Page, label: string) {
  const navigation = page.getByRole("navigation", { name: "Main navigation" });
  await navigation.waitFor();
  const name = new RegExp("^" + label + "(?: |$)");
  let target = navigation.getByRole("button", {
    name,
  });
  if (!(await target.isVisible())) {
    await navigation.getByRole("button", { name: /^More/ }).click();
    target = page
      .getByRole("dialog", { name: "More from Leam" })
      .getByRole("button", { name });
  }
  await target.click();
}
export async function settingsSection(page: Page, title: string) {
  const section = page.locator(".settings-section").filter({
    has: page.locator(":scope > summary", { hasText: new RegExp("^" + title + "$") }),
  });
  if (!(await section.getAttribute("open"))) {
    if (await section.evaluate((el) => !(el as HTMLDetailsElement).open))
      await section.locator(":scope > summary").click();
  }
}

export async function chooseConversation(
  page: Page,
  id: string,
  kind: "companion" | "coding" = "companion",
) {
  const list = page.getByRole("region", {
    name: kind === "companion" ? "Companion conversations" : "Coding sessions",
    exact: true,
  });
  const toggle = list.locator(".conversation-list-toggle");
  if ((await toggle.getAttribute("aria-expanded")) === "false")
    await toggle.click();
  await list
    .locator(`[data-thread-id="${id}"]`)
    .getByRole("button", { name: /^Open / })
    .click();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
}
