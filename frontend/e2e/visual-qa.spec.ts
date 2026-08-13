import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";

const fixture = fileURLToPath(new URL("../../backend/tests/fixtures/performance-listing-template.xlsx", import.meta.url));
const screenshotDir = fileURLToPath(new URL("../../docs/operations/screenshots/", import.meta.url));
const sizes = [
  { width: 1440, height: 900 },
  { width: 1024, height: 768 },
  { width: 390, height: 844 },
  { width: 320, height: 568 },
] as const;

test("capture populated chat and Excel workspaces at release sizes", async ({ page }) => {
  await page.setViewportSize(sizes[0]);
  await page.goto("/chat");
  await page.getByTestId("new-conversation").click();
  await page.getByTestId("message-input").fill("蓝色亮片竞赛表演服，请给出美国 Etsy Listing 表达建议。");
  await page.getByTestId("send-message").click();
  await expect(page.getByText(/已收到并保存/)).toBeVisible();

  await page.goto("/excel");
  await page.getByTestId("excel-file-input").setInputFiles(fixture);
  await expect(page.getByText("输出文件已就绪")).toBeVisible({ timeout: 20_000 });

  for (const size of sizes) {
    await page.setViewportSize(size);
    await page.goto("/chat");
    await expect(page.getByText(/已收到并保存/)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({
      path: `${screenshotDir}chat-${size.width}x${size.height}.png`,
      animations: "disabled",
    });

    await page.goto("/excel");
    await expect(page.getByText("输出文件已就绪")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({
      path: `${screenshotDir}excel-${size.width}x${size.height}.png`,
      animations: "disabled",
    });
  }
});
