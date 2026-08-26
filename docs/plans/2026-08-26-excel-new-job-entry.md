# Excel Visible New Job Entry Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Keep a clear “新建 Listing 任务” action visible whenever Excel history exists, and immediately show the newly created task as current.

**Architecture:** Reuse `ExcelDropzone` as both the existing full empty-state uploader and a compact header button so validation and file input behavior stay in one component. Keep task creation and selection in the existing store; only change presentation and regression coverage.

**Tech Stack:** Vue 3, TypeScript, Vitest, Vue Test Utils, Lucide Vue.

---

### Task 1: Lock the visible-entry behavior with tests

**Files:**
- Modify: `frontend/src/features/excel/ExcelView.spec.ts`

**Step 1: Write the failing test**

Add a test with one completed historical job that asserts:

```ts
expect(wrapper.get('[data-testid="new-excel-job"]')).toBeTruthy();
expect(wrapper.text()).not.toContain("上传另一个工作簿");
await chooseFile(wrapper, new File(["PK"], "new-costume.xlsx"));
expect(store.currentJob?.source_filename).toBe("new-costume.xlsx");
expect(wrapper.text()).toContain("new-costume.xlsx");
```

Extend the pending-upload test so the compact entry is disabled and reads “正在创建任务…” while the request is pending.

**Step 2: Run the test to verify it fails**

Run: `pnpm --dir frontend test -- --run src/features/excel/ExcelView.spec.ts`

Expected: FAIL because no `new-excel-job` control exists and the old folded uploader remains.

### Task 2: Add the compact uploader and remove the folded entry

**Files:**
- Modify: `frontend/src/features/excel/ExcelDropzone.vue`
- Modify: `frontend/src/views/ExcelView.vue`

**Step 1: Implement the compact mode**

Add an optional `compact` prop to `ExcelDropzone`. In compact mode render a real button with `data-testid="new-excel-job"`, `FilePlus2`, and copy that switches between “新建 Listing 任务” and “正在创建任务…”. Keep the same hidden `.xlsx` input, validation event, exposed `focus`, and exposed `openPicker` behavior.

**Step 2: Place it in the header**

Render the compact uploader in the Excel page header whenever a current job exists. Keep the full uploader for the no-task state. Remove the `<details>` block and make failed-task reselection open the compact uploader.

**Step 3: Preserve responsive behavior**

Keep the compact control at least 44px tall; on narrow screens let the intro wrap without clipping.

**Step 4: Run focused tests**

Run: `pnpm --dir frontend test -- --run src/features/excel/ExcelView.spec.ts`

Expected: all Excel view tests pass.

### Task 3: Verify, deploy, and publish

**Files:**
- Modify only if verification exposes a scoped defect.

**Step 1: Run the full frontend suite and build**

Run: `pnpm --dir frontend test -- --run`

Run: `pnpm --dir frontend build`

Expected: all tests and the production build pass.

**Step 2: Restart the configured local services**

Restart ports 8766 and 5173 through `scripts/start-configured.ps1`; keep the LAN proxy on 5174.

**Step 3: Verify the live page**

Require `/api/health` to return `ok`, `/excel` to return HTTP 200, and confirm the header entry is present in the built HTML application through the component tests and local page inspection.

**Step 4: Commit and push**

Run `git diff --check`, commit the implementation and plan, then push `master` to `origin`.

