import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";

const fixture = fileURLToPath(new URL("../../backend/tests/fixtures/performance-listing-template.xlsx", import.meta.url));

test("chat, Excel generation, download, and durable history", async ({ page }) => {
  await page.goto("/chat");
  await expect(page.getByRole("heading", { level: 1, name: "长期对话", exact: true })).toBeVisible();

  await page.getByTestId("new-conversation").click();
  await page.getByTestId("message-input").fill("请记住：这是一个蓝色亮片表演服测试。");
  await page.getByTestId("send-message").click();
  await expect(page.getByText("已收到并保存：请记住：这是一个蓝色亮片表演服测试。")).toBeVisible();

  await page.getByRole("link", { name: "Listing 表格" }).click();
  await expect(page.getByRole("heading", { name: "生成 Listing 表格" })).toBeVisible();
  await page.getByTestId("excel-file-input").setInputFiles(fixture);
  await expect(page.getByText("输出文件已就绪")).toBeVisible({ timeout: 20_000 });

  const downloadPromise = page.waitForEvent("download");
  await page.getByTestId("download-result").click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.xlsx$/i);
  expect(await download.createReadStream()).not.toBeNull();

  await page.reload();
  await expect(page.getByText("performance-listing-template.xlsx", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("download-result")).toBeVisible();

  await page.getByRole("link", { name: "长期对话" }).click();
  await expect(page.getByText("已收到并保存：请记住：这是一个蓝色亮片表演服测试。")).toBeVisible();
});
