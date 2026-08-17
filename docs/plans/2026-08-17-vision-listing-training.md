# Vision + Listing Training Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an auditable one-click Etsy main-image + Listing training pipeline with independent AI review and safe auto-activation, then make Excel production combine the first row image with row text and skip image-less rows.

**Architecture:** Add a backend `app.training` package for strict multimodal contracts, Etsy collection, persistence, Hermes calls, orchestration, and CLI execution. Keep knowledge state transitions inside `KnowledgeService`; the training model never writes the database. Reuse the same visual-fact schema in the employee Excel skill, where a first vision call produces structured facts and a second call generates the five Listing fields from text-preferred merged facts.

**Tech Stack:** Python 3.11, FastAPI service modules, SQLAlchemy 2 + SQLite migrations, Pydantic 2, Pillow, httpx, DrissionPage/Chrome, Hermes CLI, openpyxl inside the product, PowerShell, Vue 3 + TypeScript, pytest, Vitest.

**Approved design:** `docs/superpowers/specs/2026-08-17-vision-listing-training-design.md`

**Baseline note:** Before feature changes, frontend `pnpm vitest run` passed 75 tests and `pnpm build` passed. The full backend baseline had 258 passing, 1 skipped, 40 failures, and 70 errors caused primarily by the existing Windows temp-directory ACL snapshot check; every new focused test must pass independently, and the unrelated ACL baseline must not be represented as a feature regression.

---

### Task 1: Define strict multimodal facts and text-preferred merge rules

**Files:**
- Create: `backend/app/training/__init__.py`
- Create: `backend/app/training/schemas.py`
- Create: `backend/app/training/facts.py`
- Test: `backend/tests/test_training_facts.py`

**Step 1: Write failing schema and merge tests**

Cover valid visible fields, extra-field rejection, bounded strings/arrays, forbidden inference rejection, text-only restricted facts, visual-only safe facts, compatible values, and conflicts where text wins. Use a representative assertion:

```python
def test_merge_keeps_listing_text_when_image_conflicts():
    result = merge_facts(
        text={"colors": ["navy"], "materials": ["polyester"]},
        visual=VisualAnalysis.model_validate({
            "schema_version": 1,
            "visible_facts": {"colors": ["black"], **EMPTY_VISIBLE_FIELDS},
            "uncertain_observations": [],
            "forbidden_inferences": [],
            "image_usable": True,
        }),
    )
    assert result.facts["colors"][0].value == "navy"
    assert result.facts["colors"][0].source == "text"
    assert result.conflicts[0].field == "colors"
    assert "materials" not in result.visual_contributions
```

**Step 2: Run tests and verify red**

Run: `cd backend && uv run pytest tests/test_training_facts.py -q`
Expected: collection fails because `app.training` does not exist.

**Step 3: Implement minimal strict contracts**

Implement `VisibleFacts`, `VisualAnalysis`, `FactValue`, `FactConflict`, `MergedFacts`, `CandidateProposal`, `CandidateSet`, `ReviewItem`, and `ReviewSet` with `extra="forbid"`, Unicode/control-character cleaning, item/count limits, the five allowed knowledge kinds, and review confidence `0..1`. In `facts.py`, define:

```python
TEXT_ONLY_FIELDS = frozenset({
    "materials", "sizes", "bundle_contents", "unseen_accessories", "performance",
    "brand", "certification", "price", "inventory", "shipping",
})

def merge_facts(text: Mapping[str, Sequence[str]], visual: VisualAnalysis) -> MergedFacts:
    # Normalize values; text values are authoritative. Safe visual fields may add
    # non-duplicate compatible details. Conflicts are recorded and never override text.
```

**Step 4: Run focused tests and verify green**

Run: `cd backend && uv run pytest tests/test_training_facts.py -q`
Expected: all tests pass.

**Step 5: Commit**

```powershell
git add backend/app/training backend/tests/test_training_facts.py
git commit -m "feat: add multimodal fact contracts"
```

### Task 2: Extract safe Etsy sources and normalize the first main image

**Files:**
- Create: `backend/app/training/etsy.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Test: `backend/tests/test_training_etsy.py`

**Step 1: Write failing source tests**

Test strict shop/Listing URL normalization, workbook shop extraction and deduplication, shop-page Listing order, Product JSON-LD first image, `og:image` fallback, non-Etsy redirects, MIME/signature mismatch, decompression/pixel limits, metadata removal, SHA-256 stability, and source workbook hash preservation.

**Step 2: Run tests and verify red**

Run: `cd backend && uv run pytest tests/test_training_etsy.py -q`
Expected: imports fail for `app.training.etsy`.

**Step 3: Implement pure extraction and bounded download**

Add strict functions with dependency injection for HTTP:

```python
def extract_shop_urls(workbook: Path) -> list[str]: ...
def normalize_shop_url(value: str) -> str: ...
def normalize_listing_url(value: str) -> tuple[str, str]: ...
def extract_listing_urls(html: str) -> list[str]: ...
def extract_listing_snapshot(html: str, canonical_url: str, fetched_at: datetime) -> ListingSnapshot: ...
def select_main_image_url(html: str, page_url: str) -> str: ...
def download_main_image(url: str, destination_root: Path, client: HttpClient) -> ImageEvidence: ...
```

Use bounded reads, at most three redirects, approved HTTPS image hosts reached from Etsy metadata, Pillow `verify` + decode, maximum 10 MB and 40 megapixels, RGB normalization, metadata-free JPEG output under the resolved evidence root, and SHA-256 of normalized bytes. Move `httpx` into runtime dependencies and add `DrissionPage>=4.1,<5` for the real visible-browser collector; update the lock intentionally with `uv lock`.

**Step 4: Run focused tests and dependency check**

Run: `cd backend && uv run pytest tests/test_training_etsy.py -q`
Run: `cd backend && uv lock --check`
Expected: both pass.

**Step 5: Commit**

```powershell
git add backend/app/training/etsy.py backend/tests/test_training_etsy.py backend/pyproject.toml backend/uv.lock
git commit -m "feat: collect safe Etsy multimodal evidence"
```

### Task 3: Persist training runs, samples, and independent reviews

**Files:**
- Modify: `backend/app/db/models.py`
- Modify: `backend/app/db/migrations.py`
- Modify: `backend/app/core/config.py`
- Create: `backend/app/training/repository.py`
- Test: `backend/tests/test_training_persistence.py`
- Modify: `backend/tests/test_database.py`

**Step 1: Write failing model/migration tests**

Test clean database creation, upgrade from migration 13, required fields, unique `(listing_id, image_hash, schema_version)` success identity, indexes, bounded statuses, review-to-candidate lineage, safe resume, and `training-evidence` runtime directory creation.

**Step 2: Run tests and verify red**

Run: `cd backend && uv run pytest tests/test_training_persistence.py tests/test_database.py -q`
Expected: new tables/models are absent.

**Step 3: Add models and migration 14**

Add `TrainingRun`, `TrainingSample`, and `TrainingReview` with UUID public IDs, aware UTC times, JSON facts/conflicts/risk flags, safe error codes, foreign keys to knowledge candidates, and explicit relationships. Add migration 14 with indexes and insert/update triggers that enforce UUID, SHA-256, confidence, allowed status, and decision constraints. Extend `Settings.ensure_runtime_dirs()` with `training-evidence`.

Implement a small repository with `BEGIN IMMEDIATE` writes and methods `create_run`, `claim_sample`, `transition_sample`, `complete_run`, `successful_listing_ids`, and `successful_image_hashes`. Never overwrite a terminal record; retries create new records linked by stable idempotency fields.

**Step 4: Run migration tests and verify green**

Run: `cd backend && uv run pytest tests/test_training_persistence.py tests/test_database.py -q`
Expected: all focused tests pass.

**Step 5: Commit**

```powershell
git add backend/app/db backend/app/core/config.py backend/app/training/repository.py backend/tests/test_training_persistence.py backend/tests/test_database.py
git commit -m "feat: persist multimodal training lineage"
```

### Task 4: Add stateless Hermes stages for vision, candidate generation, and review

**Files:**
- Create: `backend/app/training/model.py`
- Test: `backend/tests/test_training_model.py`

**Step 1: Write failing gateway tests**

Use a fake `HermesAdapter` that records prompt, `session_id`, `image_path`, and source. Assert the vision call receives the normalized image, candidate/review calls receive no image, every call uses `session_id=None`, webpage data is inside an untrusted JSON envelope, unknown output fields fail, and exactly one schema-repair call is allowed.

**Step 2: Run tests and verify red**

Run: `cd backend && uv run pytest tests/test_training_model.py -q`
Expected: `TrainingModel` is missing.

**Step 3: Implement the three-stage gateway**

Implement:

```python
class TrainingModel:
    async def extract_visual_facts(self, image: Path, listing_text: dict) -> VisualAnalysis: ...
    async def generate_candidates(self, merged: MergedFacts, evidence_ref: dict) -> CandidateSet: ...
    async def review_candidates(self, candidates: CandidateSet, active_rules: dict, merged: MergedFacts) -> ReviewSet: ...
```

Use strict JSON-only prompts, bounded prompt/reply bytes, tolerant extraction of a trailing JSON object, one repair attempt, and no session reuse. The reviewer receives candidates, current active abstracts/tokens, merged facts, conflicts, and deterministic precheck results, but no generator reasoning.

**Step 4: Run tests and verify green**

Run: `cd backend && uv run pytest tests/test_training_model.py -q`
Expected: all tests pass.

**Step 5: Commit**

```powershell
git add backend/app/training/model.py backend/tests/test_training_model.py
git commit -m "feat: add independent multimodal AI stages"
```

### Task 5: Apply reviewed candidates through the knowledge transaction boundary

**Files:**
- Modify: `backend/app/knowledge/service.py`
- Modify: `backend/app/knowledge/schemas.py`
- Modify: `backend/app/db/models.py`
- Test: `backend/tests/test_training_activation.py`
- Modify: `backend/tests/test_knowledge_service.py`

**Step 1: Write failing activation tests**

Test five proposed candidates from one evidence item, AI approve + confidence `>=0.85` + no risks activates with actor `system:ai-review`, AI reject/invalid stays proposed, deterministic unsafe content becomes rejected, stale active token stays proposed, duplicate application is idempotent, and every activation links rule version → candidate → review → training sample.

**Step 2: Run tests and verify red**

Run: `cd backend && uv run pytest tests/test_training_activation.py -q`
Expected: reviewed-training API is absent.

**Step 3: Implement reviewed batch application**

Add an internal-only method that holds the existing service lock and one immediate transaction:

```python
def apply_reviewed_training_batch(
    self,
    *,
    sample_id: int,
    evidence: EvidenceInput,
    candidates: CandidateSet,
    reviews: ReviewSet,
    reviewed_active_tokens: dict[str, ActiveToken],
    trace_id: str,
) -> list[TrainingActivationResult]: ...
```

Ingest/reuse evidence; validate every candidate; capture proposed candidates; persist all reviews; compare reviewed and current active tokens; run candidate schema, policy, regression, originality, kind, confidence, and risk gates; call `_activate(..., actor="system:ai-review")` only for passes. AI non-approval and operational failures stay proposed; deterministic policy failures become rejected. Store review public ID in activation audit details without exposing raw prompts.

**Step 4: Run focused knowledge tests**

Run: `cd backend && uv run pytest tests/test_training_activation.py tests/test_knowledge_service.py -q`
Expected: focused tests pass except any already-documented machine ACL fixture; the pure service tests must be green.

**Step 5: Commit**

```powershell
git add backend/app/knowledge backend/app/db/models.py backend/tests/test_training_activation.py backend/tests/test_knowledge_service.py
git commit -m "feat: gate auto activation on independent AI review"
```

### Task 6: Build the visible-browser collector and resumable one-click orchestrator

**Files:**
- Create: `backend/app/training/browser.py`
- Create: `backend/app/training/service.py`
- Test: `backend/tests/test_training_service.py`

**Step 1: Write failing orchestration tests**

Inject fake browser, downloader, clock, sleeper, model, repository, and knowledge service. Assert shop order, first untrained Listing selection, in-run and historical deduplication, first main image only, 15–25 second delay enforcement, every persisted transition, continuation after a failed shop, no candidate call on unusable image, and complete aggregation.

**Step 2: Run tests and verify red**

Run: `cd backend && uv run pytest tests/test_training_service.py -q`
Expected: orchestration types are absent.

**Step 3: Implement browser collector and state machine**

`VisibleEtsyBrowser` uses a shared dedicated profile under DataDirectory, a visible Chrome/Edge window, Etsy warm-up, bounded waits, and returns HTML only after real-content checks. It never solves or bypasses challenges. `VisionTrainingService.run()` performs:

```text
workbook hash → shops → shop fetch → untrained Listing claim → Listing fetch
→ text snapshot → main image normalization → visual facts → deterministic merge
→ active token snapshot → candidates → independent review → reviewed batch apply
→ sample/run terminal status
```

Persist after each arrow. Use stable safe error codes and log only run ID, Listing ID, status, and code.

**Step 4: Run orchestration tests and verify green**

Run: `cd backend && uv run pytest tests/test_training_service.py -q`
Expected: all tests pass without network or real model.

**Step 5: Commit**

```powershell
git add backend/app/training/browser.py backend/app/training/service.py backend/tests/test_training_service.py
git commit -m "feat: orchestrate resumable vision training"
```

### Task 7: Provide the one-click PowerShell/CLI entry point

**Files:**
- Create: `backend/app/training/cli.py`
- Create: `scripts/train-vision-listings.ps1`
- Test: `backend/tests/test_training_cli.py`
- Modify: `docs/operations/mvp-runbook.md`

**Step 1: Write failing CLI tests**

Test required existing `.xlsx`, delay range, positive limit, default `Limit=1` safety, data directory resolution, database initialization, employee/browser/capacity preflight, source hash unchanged, safe JSON summary, and non-zero exit for no shops or failed preflight.

**Step 2: Run tests and verify red**

Run: `cd backend && uv run pytest tests/test_training_cli.py -q`
Expected: CLI module is absent.

**Step 3: Implement CLI and wrapper**

Expose `python -m app.training.cli --workbook ... --limit 1 --delay 20`. The PowerShell wrapper resolves the repository root, verifies the workbook literal path, and calls backend `uv run` without constructing shell code from workbook content. Default to one sample; require explicit `-Batch` to omit the limit. Print a bounded summary with run public ID and counts only.

**Step 4: Run CLI/help tests**

Run: `cd backend && uv run pytest tests/test_training_cli.py -q`
Run: `powershell -NoProfile -File scripts/train-vision-listings.ps1 -Help`
Expected: tests pass and help exits without changing state.

**Step 5: Commit**

```powershell
git add backend/app/training/cli.py backend/tests/test_training_cli.py scripts/train-vision-listings.ps1 docs/operations/mvp-runbook.md
git commit -m "feat: add one-click vision training command"
```

### Task 8: Make the Excel employee perform two-stage multimodal generation and skip no-image rows

**Files:**
- Create: `employee/skills/etsy-performance-listing/scripts/visual_context.py`
- Modify: `employee/skills/etsy-performance-listing/scripts/run_task.py`
- Modify: `employee/skills/etsy-performance-listing/scripts/inspect_workbook.py`
- Modify: `employee/skills/etsy-performance-listing/SKILL.md`
- Modify: `employee/skills/etsy-performance-listing/references/output-contract.md`
- Modify: `backend/tests/test_excel_skill.py`

**Step 1: Write failing Excel skill tests**

Create deterministic workbooks with an image row, no-image row, and multi-image row. Assert no-image emits `row_skipped/missing_product_image` and never invokes Hermes; the image row invokes Hermes first with exactly the first image for visual JSON, then without image for final Listing JSON; row text wins conflicts; a visual schema failure gets one repair; manifest stores sanitized visual context; output writes only successful rows; source SHA-256 stays unchanged; all-skipped returns `no_rows_with_images` without publishing an artifact.

**Step 2: Run tests and verify red**

Run: `cd backend && uv run pytest tests/test_excel_skill.py -q`
Expected: no `row_skipped` or visual stage exists.

**Step 3: Implement shared employee visual context**

`visual_context.py` mirrors the backend schema with no backend import dependency. Update `run_task.py` to call a strict visual prompt with `--image` and row candidate fields, parse/repair it, merge row text deterministically, save a sanitized `visual-context.json`, then call the existing generation stage without `--image` using merged facts and active knowledge. Do not add raw image bytes or paths to the generation prompt.

Before `row_started`, skip rows without `image_paths`; emit `row_skipped` with row identity and the stable public message. Only first `image_paths[0]` is used. Preserve fixed output schema and originality guard.

**Step 4: Run Excel skill tests and verify green**

Run: `cd backend && uv run pytest tests/test_excel_skill.py -q`
Expected: all Excel skill tests pass.

**Step 5: Commit**

```powershell
git add employee/skills/etsy-performance-listing backend/tests/test_excel_skill.py
git commit -m "feat: combine Excel row images with row facts"
```

### Task 9: Carry skipped-row events safely through backend and frontend

**Files:**
- Modify: `backend/app/excel_jobs/runner.py`
- Modify: `backend/app/excel_jobs/service.py`
- Modify: `backend/tests/test_excel_jobs_api.py`
- Modify: `frontend/src/api/excel.ts`
- Modify: `frontend/src/features/excel/excel.store.ts`
- Modify: `frontend/src/features/excel/JobProgress.vue`
- Modify: `frontend/src/features/excel/ExcelView.spec.ts`

**Step 1: Write failing protocol/UI tests**

Test acceptance of bounded `row_skipped`, persistence as `worker_row_skipped`, monotonic progress, aggregation of the safe missing-image warning, rejection of paths/URLs/control characters in skip messages, and visible Chinese copy “已跳过：缺少商品图片”.

**Step 2: Run tests and verify red**

Run: `cd backend && uv run pytest tests/test_excel_jobs_api.py -q`
Run: `cd frontend && pnpm vitest run src/features/excel/ExcelView.spec.ts`
Expected: skip event is rejected/ignored.

**Step 3: Implement protocol and presentation**

Add `row_skipped` to the worker allowlist and row identity validation. Map it to a bounded public event and warning; do not expose operation paths. Add the frontend event type and a skipped-row counter/message in `JobProgress.vue`, while preserving generic warning handling and terminal job semantics.

**Step 4: Run focused backend/frontend tests**

Run: `cd backend && uv run pytest tests/test_excel_jobs_api.py -q`
Run: `cd frontend && pnpm vitest run src/features/excel/ExcelView.spec.ts`
Expected: focused tests pass, subject to the documented machine ACL issue only for fixtures that start the full app.

**Step 5: Commit**

```powershell
git add backend/app/excel_jobs backend/tests/test_excel_jobs_api.py frontend/src
git commit -m "feat: report Excel rows skipped for missing images"
```

### Task 10: Make training lineage portable and provision the new employee asset

**Files:**
- Modify: `backend/app/migration/contracts.py`
- Modify: `backend/app/migration/exporter.py`
- Modify: `backend/app/migration/importer.py`
- Modify: `backend/tests/test_migration.py`
- Modify: `scripts/provision-employee.ps1`
- Modify: `scripts/verify-employee.ps1`
- Modify: `backend/tests/test_employee_assets.py`

**Step 1: Write failing migration/provisioning tests**

Test training run/sample/review metadata round-trip without raw image files, foreign-key lineage remapping, hash/fact/review preservation, unsafe import rejection, `visual_context.py` asset hash inclusion, copy behavior, and verifier allowlist behavior.

**Step 2: Run tests and verify red**

Run: `cd backend && uv run pytest tests/test_migration.py tests/test_employee_assets.py -q`
Expected: new entities/asset are omitted.

**Step 3: Extend portable contracts and provisioning**

Add schema-versioned training records to export/import while excluding local absolute paths and raw images by default. Remap sample candidate/review IDs on import and validate hashes/decisions. Add `visual_context.py` to `Get-AssetHashes`, provisioning copy list, verifier mapping, and strict allowed entries.

**Step 4: Run focused tests and read-only verifier**

Run: `cd backend && uv run pytest tests/test_migration.py tests/test_employee_assets.py -q`
Run: `powershell -NoProfile -File scripts/verify-employee.ps1`
Expected: automated focused tests pass; the current live Profile may report an expected repository/manifest mismatch until the verified deployment step.

**Step 5: Commit**

```powershell
git add backend/app/migration backend/tests/test_migration.py backend/tests/test_employee_assets.py scripts/provision-employee.ps1 scripts/verify-employee.ps1
git commit -m "feat: migrate vision training lineage and assets"
```

### Task 11: Run complete automated verification and code review

**Files:**
- Modify as required by failures: only files already in scope
- Add tests only when a discovered regression lacks coverage

**Step 1: Run all new focused suites together**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_training_facts.py tests/test_training_etsy.py tests/test_training_persistence.py tests/test_training_model.py tests/test_training_activation.py tests/test_training_service.py tests/test_training_cli.py tests/test_excel_skill.py -q
```

Expected: all pass.

**Step 2: Run frontend verification**

Run: `cd frontend && pnpm vitest run`
Run: `cd frontend && pnpm build`
Expected: all tests and production build pass.

**Step 3: Run backend suite and classify only proven baseline failures**

Run: `cd backend && uv run pytest -q`
Expected: feature-related tests pass. If the existing ACL problem remains, compare exact failing set/cause with the recorded baseline; fix any new failure and never dismiss it as baseline without evidence.

**Step 4: Run diff/security checks**

Run: `git diff --check`
Run: `rg -n "config\.yaml|api[_-]?key|access[_-]?token|cookie" backend/app/training scripts/train-vision-listings.ps1`
Run: `git status --short`
Expected: no whitespace errors, secrets, raw credentials, unexpected artifacts, or edits outside scope.

**Step 5: Commit any verification fixes**

If verification required no source change, do not create an empty commit. If a prior task needed a fix, stage the same explicit files listed by that task and commit them with `git commit -m "test: harden multimodal training workflow"`.

### Task 12: Perform isolated real Limit-1 training and Excel-copy acceptance

**Files/Artifacts:**
- Source, read only: `C:\Users\25941\Downloads\Etsy表演服工作任务清单表-飞书云文档.xlsx`
- Create: a conversation-specific validation copy/output directory outside Downloads
- Do not modify: source workbook, port 8765 service, Hermes credentials/config

**Step 1: Hash and inspect the source read-only**

Record source SHA-256, confirm shop URLs exist, and do not author the source workbook. Use the Spreadsheets skill/artifact runtime for any workbook copy/edit or visual verification; run its required operation marker exactly once before the first authored validation workbook.

**Step 2: Run one real training sample**

Run:

```powershell
.\scripts\train-vision-listings.ps1 -Workbook "C:\Users\25941\Downloads\Etsy表演服工作任务清单表-飞书云文档.xlsx" -Limit 1
```

Expected: one Listing reaches a terminal state; a success has nonempty listing/image hashes, visual facts, five review rows, and only dual-gate-approved candidates active.

**Step 3: Audit the database without exposing raw evidence**

Query safe counts/IDs/statuses and assert rule versions link back to `system:ai-review`. Recompute the source workbook SHA-256 and assert unchanged. Do not print raw Listing snapshots, cookies, image paths, prompts, or credentials.

**Step 4: Run production on a workbook copy with image/text and a no-image control row**

Use a copy/controlled validation workbook, never the Downloads source. Confirm the image row sends the first image to the visual call and produces five fields, the no-image row is skipped and unchanged, output opens, source-copy hash remains unchanged, and result is a separate file. Use artifact-tool inspection plus at least one rendered visual pass for the output workbook.

**Step 5: Deploy employee skill assets safely**

Stop only the 8766 application if needed, back up the existing `etsy-performance-us` skill directory and provisioning manifest, copy the verified repository skill files including `visual_context.py`, update the manifest asset hashes without reading `config.yaml`, restart 8766, and run the read-only verifier/model check. Never touch port 8765.

**Step 6: Final repository and live verification**

Run focused tests again, verify live employee online, verify active knowledge invariants (exactly one active rule per kind), and inspect git status/log. Commit only repository changes; runtime DB, browser profile, images, validation workbooks, and backups stay outside git.

**Step 7: Finish the branch**

Use the verification-before-completion and finishing-a-development-branch skills. Merge the feature branch only after all acceptance evidence is current. Report the actual capability boundary: Etsy performance apparel and adjacent categories with required row image + text, not arbitrary products without the input contract.
