# Excel Transient Row Recovery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Recover a row that encounters two transient model failures without sacrificing bounded parallel processing or publishing partial workbooks.

**Architecture:** Keep the existing bounded parallel first pass. Reuse a single bounded-attempt helper for sequential jobs and for the remaining post-parallel retry budget, while retrying only the existing transient error codes.

**Tech Stack:** Python 3.11, pytest, openpyxl, Codex/Hermes subprocess runner.

---

### Task 1: Add the regression test

**Files:**
- Modify: `backend/tests/test_excel_skill.py`

**Step 1: Write the failing test**

Add a two-row parallel test in which SKU-1 fails with `OSError` on its first two image calls and succeeds on the third. Assert three attempts and a complete two-row output workbook.

**Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_excel_skill.py::test_parallel_row_recovery_uses_remaining_transient_retry_budget -q`

Expected: FAIL because the current parallel recovery performs only one post-batch attempt and raises `rows_failed`.

### Task 2: Implement the bounded retry budget

**Files:**
- Modify: `employee/skills/etsy-performance-listing/scripts/run_task.py`

**Step 1: Write minimal implementation**

Set the total row attempt budget to three. Give the sequential path the full budget and give parallel recovery only the attempts remaining after its initial parallel call. Stop immediately on a non-retryable error.

**Step 2: Run targeted tests**

Run: `python -m pytest backend/tests/test_excel_skill.py -q`

Expected: PASS.

### Task 3: Verify and deploy the employee copy

**Files:**
- Synchronize the changed skill script into the configured `etsy-performance-us` Hermes profile.
- Update the profile manifest through the repository's existing synchronization/bootstrap workflow.

**Step 1: Run full verification**

Run the backend test suite, frontend test suite, frontend build, and `git diff --check`.

Expected: all commands exit zero.

**Step 2: Restart and smoke test**

Restart the configured local environment, confirm ports 8766 and 5174, then resubmit the preserved source workbook and verify progress advances without the prior two-row timeout failure.

**Step 3: Commit and push**

Commit only the recovery, tests, documentation, and synchronized profile-manifest changes; push the active branch to the configured GitHub remote.

### Task 4: Align final artifact validation with visible-sheet selection

**Files:**
- Modify: `backend/tests/test_excel_jobs_api.py`
- Modify: `backend/app/excel_jobs/storage.py`

**Step 1: Write the failing test**

Create an otherwise valid artifact containing a hidden copy of the product worksheet. Verify that the hidden duplicate headers do not invalidate the artifact.

**Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_excel_jobs_api.py::test_artifact_contract_ignores_hidden_duplicate_headers -q`

Expected: FAIL because the current validator counts all worksheets.

**Step 3: Write minimal implementation**

Skip every non-visible worksheet while counting fixed output headers in `validate_artifact`. Keep the existing exact-one check across visible worksheets.

**Step 4: Verify and rerun the real workbook**

Run the artifact tests and full backend suite, then resubmit the preserved source and require a completed job with a validated downloadable artifact.

### Task 5: Recover exhausted model-response errors after parallel generation

**Files:**
- Modify: `backend/tests/test_excel_skill.py`
- Modify: `employee/skills/etsy-performance-listing/scripts/run_task.py`
- Modify: `scripts/start-configured.ps1`

**Step 1: Write the failing test**

Simulate one parallel row whose visual call succeeds but whose two Listing calls return malformed output. A later serial whole-row attempt returns valid output. Require a complete workbook.

**Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_excel_skill.py::test_parallel_row_recovery_retries_exhausted_model_output_errors -q`

Expected: FAIL with `rows_failed` because exhausted model output errors are not currently in the parallel recovery set.

**Step 3: Write minimal implementation**

Add a parallel-only recovery set containing the existing transient process errors plus exhausted visual JSON, Listing JSON, schema, and originality errors. Do not add deterministic image or workbook errors. Set configured production row workers to two.

**Step 4: Verify and rerun**

Run the Excel skill tests and full backend suite, restart with two workers, and require the real workbook to reach 100% with a downloadable artifact.

### Task 6: Keep long Excel streams connected and extend bounded row recovery

**Files:**
- Create: `frontend/src/api/excel.spec.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/excel.ts`
- Modify: `backend/tests/test_excel_skill.py`
- Modify: `employee/skills/etsy-performance-listing/scripts/run_task.py`

**Step 1: Add failing regressions**

Assert that the Excel event stream does not create a 190-second absolute timer. Add row tests for a transient `image_unusable` result and a row that succeeds on its fifth whole-row attempt.

**Step 2: Implement the bounded fix**

Allow an event stream to opt out of the client timer and use that option only for Excel jobs. Raise the total row attempt budget from three to five and treat model `image_unusable` as retryable; preserve the final failure and no-partial-artifact behavior after the budget is exhausted.

**Step 3: Perform real acceptance**

Restart the configured services and resubmit the exact 50,132,885-byte source workbook. Require 100%, download through the LAN proxy, match the artifact SHA-256, validate every generated row, confirm that only the five target columns changed, and confirm all embedded images and worksheet states remain intact.
