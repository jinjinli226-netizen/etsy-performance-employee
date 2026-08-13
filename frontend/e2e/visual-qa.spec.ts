import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { mkdir } from "node:fs/promises";

const fixture = fileURLToPath(new URL("../../backend/tests/fixtures/performance-listing-template.xlsx", import.meta.url));
const currentScreenshotDir = fileURLToPath(new URL("../../test-results/visual-current/", import.meta.url));
const baselineScreenshotDir = fileURLToPath(new URL("../../docs/operations/screenshots/", import.meta.url));
const screenshotDir = process.env.UPDATE_VISUAL_BASELINES === "1" ? baselineScreenshotDir : currentScreenshotDir;
const sizes = [
  { width: 1440, height: 900 },
  { width: 1024, height: 768 },
  { width: 390, height: 844 },
  { width: 320, height: 568 },
] as const;

test("capture populated chat and Excel workspaces at release sizes", async ({ page }) => {
  expect(screenshotDir).toBe(process.env.UPDATE_VISUAL_BASELINES === "1" ? baselineScreenshotDir : currentScreenshotDir);
  await mkdir(screenshotDir, { recursive: true });
  await page.addInitScript(() => {
    const FixedDate = class extends Date {
      constructor(...args: ConstructorParameters<typeof Date>) {
        super(...(args.length ? args : ["2026-08-14T02:43:00.000Z"]));
      }
      static now() { return new Date("2026-08-14T02:43:00.000Z").valueOf(); }
    };
    Object.defineProperty(window, "Date", { value: FixedDate });
  });
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
    if (size.width <= 390) {
      const trigger = await page.getByTestId("conversation-mobile-trigger").boundingBox();
      const title = await page.getByTestId("conversation-title").boundingBox();
      const learning = await page.getByTestId("learning-status").boundingBox();
      expect(trigger && title && learning).toBeTruthy();
      expect(trigger!.x + trigger!.width).toBeLessThanOrEqual(title!.x);
      expect(title!.x + title!.width).toBeLessThanOrEqual(learning!.x);
    }
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
