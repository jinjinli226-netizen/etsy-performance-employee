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
