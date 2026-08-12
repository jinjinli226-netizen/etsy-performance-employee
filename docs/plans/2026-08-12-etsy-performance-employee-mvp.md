# Etsy Performance Employee MVP Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a local Vue 3 + FastAPI application that connects to a brand-new isolated Hermes performance-costume employee, supports persistent chat and attachments, and lets the employee transform uploaded Excel workbooks into new copies containing the five fixed Etsy Listing fields.

**Architecture:** The website is a thin local shell. It stores chats, jobs, knowledge, and artifacts, but it does not understand dynamic Excel business headers; the `etsy-performance-us` Hermes Profile and its `etsy-performance-listing` skill own workbook interpretation and generation. Raw competitor pages are evidence-only, while generation can retrieve only approved abstract knowledge.

**Tech Stack:** Vue 3, TypeScript, Vite, Pinia, Vue Router, VueUse, Lucide, FastAPI, Pydantic, SQLAlchemy 2, Alembic, SQLite/FTS5, openpyxl, pytest, Vitest, Vue Test Utils, Playwright, Hermes Agent CLI v0.18.2.

---

## Delivery constraints

- Work in the dedicated new repository `D:\元序AI\etsy-performance-employee`.
- Optimize for the fastest usable first version.
- Do not add smoke tests, load tests, stress tests, microservices, cloud deployment, Etsy publishing, or model fine-tuning.
- Keep focused unit, integration, and critical-flow end-to-end tests because “no known blocking bugs” is the release criterion.
- Preserve the source workbook. Every successful task produces a new copy.
- Use `@test-driven-development` for feature code, `@building-immersive-web-interfaces` for UI implementation, and `@verification-before-completion` before claiming completion.
- Commit after each task or coherent subtask.

## Target repository structure

```text
etsy-performance-employee/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── chat/
│   │   ├── core/
│   │   ├── db/
│   │   ├── employee/
│   │   ├── excel_jobs/
│   │   ├── knowledge/
│   │   ├── migration/
│   │   └── main.py
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── features/chat/
│   │   ├── features/excel/
│   │   ├── layouts/
│   │   ├── router/
│   │   ├── stores/
│   │   ├── styles/
│   │   └── views/
│   └── package.json
├── employee/
│   ├── SOUL.md
│   └── skills/etsy-performance-listing/
│       ├── SKILL.md
│       ├── scripts/inspect_workbook.py
│       ├── scripts/write_workbook.py
│       └── references/output-contract.md
├── scripts/
│   ├── provision-employee.ps1
│   ├── dev.ps1
│   └── package-employee.ps1
├── docs/
└── .gitignore
```

### Task 1: Scaffold the local application

**Files:**
- Create: `.gitignore`
- Create: `README.md`
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/tests/test_health.py`
- Create: `frontend/` with Vite Vue TypeScript scaffold
- Create: `pnpm-workspace.yaml`
- Create: `scripts/dev.ps1`

**Step 1: Write the failing backend health test**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok() -> None:
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

**Step 2: Run the test to verify it fails**

Run:

```powershell
cd backend
python -m pytest tests/test_health.py -q
```

Expected: FAIL because `app.main` or the route does not exist.

**Step 3: Add the minimal FastAPI app**

```python
from fastapi import FastAPI

app = FastAPI(title="Etsy Performance Employee")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

**Step 4: Scaffold Vue and add a frontend render test**

Run:

```powershell
pnpm create vite frontend --template vue-ts
cd frontend
pnpm install
pnpm add vue-router pinia @vueuse/core lucide-vue-next
pnpm add -D vitest @vue/test-utils jsdom playwright @playwright/test
```

Create `frontend/src/App.spec.ts` asserting the app renders the two navigation labels `Excel 自动化` and `长期对话`.

**Step 5: Implement the minimal app shell and development script**

`scripts/dev.ps1` starts FastAPI on `127.0.0.1:8765` and Vite on `127.0.0.1:5173` in two hidden child processes, records their PIDs, and stops both on exit.

**Step 6: Run focused tests**

Run:

```powershell
cd backend
python -m pytest tests/test_health.py -q
cd ..\frontend
pnpm vitest run src/App.spec.ts
pnpm build
```

Expected: all pass and the frontend production build succeeds.

**Step 7: Commit**

```powershell
git add .gitignore README.md backend frontend pnpm-workspace.yaml scripts/dev.ps1
git commit -m "chore: scaffold local employee app"
```

### Task 2: Add settings, persistence, and domain contracts

**Files:**
- Create: `backend/app/core/config.py`
- Create: `backend/app/db/base.py`
- Create: `backend/app/db/session.py`
- Create: `backend/app/db/models.py`
- Create: `backend/app/db/init_db.py`
- Create: `backend/app/chat/schemas.py`
- Create: `backend/app/excel_jobs/schemas.py`
- Create: `backend/app/knowledge/schemas.py`
- Create: `backend/tests/test_database.py`
- Create: `backend/tests/test_contracts.py`

**Step 1: Write failing persistence tests**

Test that SQLite can persist and reload:

- one conversation and message;
- one Excel job and artifact;
- one knowledge candidate and one rule version.

Use a temporary SQLite path for every test.

**Step 2: Define explicit status enums and schemas**

Required enums:

```python
class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    needs_review = "needs_review"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class KnowledgeStatus(str, Enum):
    proposed = "proposed"
    testing = "testing"
    active = "active"
    rejected = "rejected"
    rolled_back = "rolled_back"
```

`GeneratedListingFields` must expose only:

```python
class GeneratedListingFields(BaseModel):
    head_titles: str
    tags: list[str]
    specification: str
    category: str
    instructions_for_buyers: str
    confidence: float = Field(ge=0, le=1)
    fact_warnings: list[str] = []
    quality_warnings: list[str] = []
    rule_version: str
```

**Step 3: Implement SQLAlchemy models**

Create tables for conversations, messages, attachments, Excel jobs, artifacts, knowledge candidates, knowledge patterns, rule versions, feedback events, and audit events. Store timestamps in UTC and expose them as ISO 8601 through the API.

**Step 4: Run focused tests**

Run:

```powershell
cd backend
python -m pytest tests/test_database.py tests/test_contracts.py -q
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add backend/app/core backend/app/db backend/app/chat/schemas.py backend/app/excel_jobs/schemas.py backend/app/knowledge/schemas.py backend/tests
git commit -m "feat: add local persistence and contracts"
```

### Task 3: Create the isolated Hermes employee assets and provisioner

**Files:**
- Create: `employee/SOUL.md`
- Create: `employee/skills/etsy-performance-listing/SKILL.md`
- Create: `employee/skills/etsy-performance-listing/references/output-contract.md`
- Create: `scripts/provision-employee.ps1`
- Create: `scripts/verify-employee.ps1`
- Create: `backend/tests/test_employee_assets.py`

**Step 1: Write failing asset tests**

Tests must assert:

- `SOUL.md` names only the Etsy performance-costume role;
- it forbids invented product facts and competitor copying;
- the skill owns dynamic workbook interpretation;
- all five fixed headers appear exactly once in the output contract;
- no API key, Cookie, Token, or unrelated employee name appears.

**Step 2: Write the employee SOUL and skill**

The SOUL must establish:

- Chinese internal communication and English Listing output;
- long-term teaching conversation versus isolated workbook-row tasks;
- raw competitor evidence cannot be used during generation;
- only active abstract knowledge can be retrieved;
- the source workbook is immutable;
- five fixed output headers are hard rules;
- uncertainty becomes a warning, never an invented fact.

**Step 3: Implement the provisioner**

`scripts/provision-employee.ps1` must:

1. Refuse to run if Profile `etsy-performance-us` already exists unless `-VerifyOnly` is supplied.
2. Hash default `SOUL.md`, `config.yaml`, and `.env` without printing file contents.
3. Run `hermes profile create etsy-performance-us --description ...` without `--clone`.
4. Configure `terminal.backend=local`, Profile workspace, `terminal.home_mode=profile`, memory, and write approvals.
5. Copy only repository-owned `SOUL.md` and the dedicated skill into the new Profile.
6. Accept model/provider/base URL as explicit non-secret parameters.
7. Read the API key interactively as a secure string when configuration is requested.
8. Recompute and compare default hashes.
9. Never print or export credentials.

**Step 4: Implement verification**

`scripts/verify-employee.ps1` checks Profile configuration, dedicated paths, skill presence, absence of default memories, minimal model response `PROFILE_READY`, and `hermes doctor`. It reports optional Doctor warnings separately from blocking failures.

**Step 5: Run asset tests without creating the Profile**

Run:

```powershell
cd backend
python -m pytest tests/test_employee_assets.py -q
```

Expected: PASS.

**Step 6: Provision and verify the real Profile**

Run interactively after non-secret model parameters are confirmed:

```powershell
.\scripts\provision-employee.ps1
.\scripts\verify-employee.ps1
```

Expected: new isolated Profile exists, minimum model call passes, and default hashes are unchanged.

**Step 7: Commit**

```powershell
git add employee scripts/provision-employee.ps1 scripts/verify-employee.ps1 backend/tests/test_employee_assets.py
git commit -m "feat: add isolated Hermes employee profile assets"
```

### Task 4: Build the Hermes subprocess adapter and persistent chat

**Files:**
- Create: `backend/app/employee/adapter.py`
- Create: `backend/app/employee/events.py`
- Create: `backend/app/chat/service.py`
- Create: `backend/app/api/chat.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/fakes/fake_hermes.py`
- Create: `backend/tests/test_employee_adapter.py`
- Create: `backend/tests/test_chat_api.py`

**Step 1: Write failing adapter tests**

Test exact command construction:

```python
assert command[:4] == ["hermes", "-p", "etsy-performance-us", "chat"]
assert "--resume" in command_when_session_exists
assert "--image" in command_when_image_attached
assert "--yolo" not in command
```

Also test timeout, non-zero exit, malformed output, and cancellation.

**Step 2: Implement `HermesAdapter`**

Expose:

```python
class HermesAdapter(Protocol):
    async def send(
        self,
        prompt: str,
        session_id: str | None,
        image_path: Path | None,
        source: str,
    ) -> EmployeeReply: ...
```

Use `asyncio.create_subprocess_exec`, never shell string concatenation. Capture stdout/stderr separately, redact paths and credentials in logs, and terminate the process on cancellation.

**Step 3: Add persistent chat APIs**

Required endpoints:

- `POST /api/conversations`
- `GET /api/conversations`
- `GET /api/conversations/{id}/messages`
- `POST /api/conversations/{id}/messages`
- `POST /api/attachments`
- `GET /api/events/{operation_id}` using SSE for progress and final events

Files are copied into a conversation-scoped directory under application data. Non-image files are referenced by safe local path in the employee prompt; one image can be passed through `--image` in MVP.

**Step 4: Parse employee event envelopes**

The employee may append hidden machine-readable events:

```json
{"event":"knowledge_candidate","payload":{"kind":"keyword_pattern","summary":"...","confidence":0.9}}
```

Strip envelopes from the visible assistant message and persist them separately. Reject unknown event types and oversized payloads.

**Step 5: Run focused tests**

Run:

```powershell
cd backend
python -m pytest tests/test_employee_adapter.py tests/test_chat_api.py -q
```

Expected: PASS using the fake Hermes process.

**Step 6: Commit**

```powershell
git add backend/app/employee backend/app/chat backend/app/api/chat.py backend/app/main.py backend/tests
git commit -m "feat: add persistent Hermes chat adapter"
```

### Task 5: Implement the employee-owned Excel skill

**Files:**
- Create: `employee/skills/etsy-performance-listing/scripts/inspect_workbook.py`
- Create: `employee/skills/etsy-performance-listing/scripts/write_workbook.py`
- Create: `employee/skills/etsy-performance-listing/scripts/validate_output.py`
- Create: `employee/skills/etsy-performance-listing/scripts/run_task.py`
- Create: `backend/tests/fixtures/performance-listing-template.xlsx`
- Create: `backend/tests/test_excel_skill.py`

**Step 1: Add a sanitized workbook fixture**

Copy the user-provided workbook structure into a test fixture without private URLs or business-sensitive values. Preserve representative formatting, an embedded image anchor, the instruction row, two product rows, and the five fixed output headers.

**Step 2: Write failing skill tests**

Tests must prove:

- five output columns are found by normalized exact header, not column letter;
- other headers can move, be renamed, be added, or be removed;
- one product yields one row task and two products yield two isolated row tasks;
- irrelevant cost/logistics fields are omitted from semantic context;
- an embedded row image is extracted to a task-specific path;
- missing or duplicate fixed output headers return a structured error;
- the source workbook SHA-256 never changes;
- the result workbook retains styles, formulas, hyperlinks, and images;
- only five output cells per processed row change.

**Step 3: Implement workbook inspection**

`inspect_workbook.py` returns a manifest, not generated content:

```json
{
  "sheet": "listing自动化",
  "source_sha256": "...",
  "output_columns": {"head titles": 18},
  "rows": [
    {
      "row_number": 3,
      "row_id": "sha256-of-row-context",
      "candidate_fields": [{"header": "SKU", "value": "XX260064"}],
      "image_paths": []
    }
  ]
}
```

Semantic relevance decisions are performed by Hermes under the skill instructions; the website never consumes `candidate_fields`.

**Step 4: Implement controlled workbook writing**

`write_workbook.py` must:

1. copy source to a new batch filename;
2. reopen the copy;
3. revalidate source hash and fixed output mapping;
4. write validated `GeneratedListingFields` into the matching row;
5. save atomically through a temporary file;
6. return output hash and changed-cell list.

**Step 5: Implement the task runner**

For each row, `run_task.py` constructs a prompt containing only that row manifest, active abstract knowledge, current rules, and output JSON schema. It calls:

```text
hermes -p etsy-performance-us chat -Q --source tool
```

and includes `--image` when the row has one extracted primary image. Each row starts a new programmatic session. Parse, validate, and retry malformed JSON once. Never provide raw competitor text.

**Step 6: Run focused tests**

Run:

```powershell
cd backend
python -m pytest tests/test_excel_skill.py -q
```

Expected: PASS with fake Hermes outputs and no source workbook mutation.

**Step 7: Commit**

```powershell
git add employee/skills/etsy-performance-listing/scripts backend/tests/fixtures backend/tests/test_excel_skill.py
git commit -m "feat: add employee-owned Excel listing skill"
```

### Task 6: Add Excel upload, job execution, events, and download

**Files:**
- Create: `backend/app/excel_jobs/storage.py`
- Create: `backend/app/excel_jobs/runner.py`
- Create: `backend/app/excel_jobs/service.py`
- Create: `backend/app/api/excel_jobs.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_excel_jobs_api.py`

**Step 1: Write failing API tests**

Test:

- `.xlsx` upload creates a queued job;
- unsupported extension and oversized file are rejected;
- source is stored read-only with SHA-256;
- the runner launches only the employee skill command, not workbook semantic logic;
- job events progress from queued to running to completed or failed;
- completed artifact can be downloaded;
- failed job never exposes a partial workbook;
- original upload hash is unchanged.

**Step 2: Implement the thin job service**

Required endpoints:

- `POST /api/excel-jobs`
- `GET /api/excel-jobs`
- `GET /api/excel-jobs/{id}`
- `GET /api/excel-jobs/{id}/events`
- `POST /api/excel-jobs/{id}/cancel`
- `GET /api/excel-jobs/{id}/download`

The job service stores files, launches `run_task.py`, persists progress JSON lines, and verifies final artifact hash/openability/five fixed headers. It does not inspect dynamic input semantics.

**Step 3: Add crash-safe output handling**

Write to an operation-scoped temporary directory and move the final workbook into the artifact directory only after validation. On cancellation or failure, remove only the verified temporary directory inside the job workspace.

**Step 4: Run focused tests**

Run:

```powershell
cd backend
python -m pytest tests/test_excel_jobs_api.py -q
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add backend/app/excel_jobs backend/app/api/excel_jobs.py backend/app/main.py backend/tests/test_excel_jobs_api.py
git commit -m "feat: add Excel job and artifact APIs"
```

### Task 7: Add competitor learning, knowledge promotion, and originality guard

**Files:**
- Create: `backend/app/knowledge/service.py`
- Create: `backend/app/knowledge/promotion.py`
- Create: `backend/app/knowledge/originality.py`
- Create: `backend/app/api/knowledge.py`
- Modify: `backend/app/chat/service.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_knowledge_service.py`
- Create: `backend/tests/test_originality.py`

**Step 1: Write failing knowledge boundary tests**

Tests must prove:

- raw Etsy URL, snapshot text, and competitor title stay evidence-only;
- generation retrieval returns only active abstract patterns;
- a candidate needs at least 3 independent evidence IDs or 3 accepted edits, confidence ≥ 0.85, no hard-rule conflict, and passing regression checks before auto-activation;
- medium-confidence candidates remain proposed;
- rollback restores the previous active version;
- generated output overly similar to raw evidence is rejected without returning the matched competitor text to the generator.

**Step 2: Implement candidate ingestion**

Chat employee events create sanitized candidates containing pattern kind, abstract summary, confidence, evidence IDs, and source timestamps. Never store secrets or page instructions as knowledge.

**Step 3: Implement promotion and rollback**

Expose:

- `GET /api/knowledge`
- `GET /api/knowledge/candidates`
- `POST /api/knowledge/candidates/{id}/approve`
- `POST /api/knowledge/candidates/{id}/reject`
- `POST /api/knowledge/patterns/{id}/rollback`

Every transition creates an audit event.

**Step 4: Implement the originality guard**

Use normalized token shingles for MVP. Compare generated title, specification, and buyer instructions against evidence-only text. Return only a boolean, score, and evidence ID to internal validation; do not expose matched text to the generation prompt.

**Step 5: Run focused tests**

Run:

```powershell
cd backend
python -m pytest tests/test_knowledge_service.py tests/test_originality.py -q
```

Expected: PASS.

**Step 6: Commit**

```powershell
git add backend/app/knowledge backend/app/api/knowledge.py backend/app/chat/service.py backend/app/main.py backend/tests
git commit -m "feat: add controlled employee learning"
```

### Task 8: Add migration export and import

**Files:**
- Create: `backend/app/migration/exporter.py`
- Create: `backend/app/migration/importer.py`
- Create: `backend/app/api/migration.py`
- Create: `scripts/package-employee.ps1`
- Create: `backend/tests/test_migration.py`

**Step 1: Write failing migration tests**

Test that an export contains:

- SOUL and dedicated skill files;
- conversations and messages;
- approved knowledge, rules, feedback, and audit metadata;
- schema version, manifest, and SHA-256 checksums.

Test that it excludes:

- `.env`;
- API keys, Tokens, Cookies, browser profile data;
- absolute machine paths;
- other Hermes Profiles.

**Step 2: Implement portable export**

Write JSONL files and a manifest into a temporary package directory, copy repository-owned employee assets, then create a ZIP. Refuse export if a secret-pattern scan matches likely credentials.

**Step 3: Implement import validation**

Validate schema version, checksums, Profile ID, and path safety before writing. Import into a staging directory and commit to the live data directory only after all checks pass. Rebuild FTS indexes after import.

**Step 4: Run focused tests**

Run:

```powershell
cd backend
python -m pytest tests/test_migration.py -q
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add backend/app/migration backend/app/api/migration.py scripts/package-employee.ps1 backend/tests/test_migration.py
git commit -m "feat: add portable employee packages"
```

### Task 9: Implement the Vue workspace shell

**Files:**
- Create: `frontend/src/styles/tokens.css`
- Create: `frontend/src/styles/base.css`
- Create: `frontend/src/router/index.ts`
- Create: `frontend/src/layouts/WorkspaceLayout.vue`
- Create: `frontend/src/components/AppSidebar.vue`
- Create: `frontend/src/components/EmployeeStatus.vue`
- Create: `frontend/src/views/ChatView.vue`
- Create: `frontend/src/views/ExcelView.vue`
- Modify: `frontend/src/App.vue`
- Create: `frontend/src/layouts/WorkspaceLayout.spec.ts`

**Step 1: Write failing layout tests**

Assert:

- exactly two primary navigation items exist;
- active route is visible;
- sidebar can collapse;
- mobile menu has an accessible label;
- employee status supports online, busy, offline, and error states.

**Step 2: Implement the Deepstage workspace profile**

Use the approved tokens:

```css
:root {
  --canvas: #08090b;
  --canvas-soft: #0d0f12;
  --surface: #12151a;
  --surface-raised: #171b21;
  --border: rgba(255, 255, 255, 0.09);
  --text: #f5f7fa;
  --text-secondary: #a7adb7;
  --text-muted: #737a86;
  --accent: #ff7a1a;
  --success: #2ccb8c;
  --warning: #f0b44d;
  --danger: #f06464;
}
```

No marketing hero, gradient, glassmorphism, nested cards, or decorative animation. The first viewport is the working application.

**Step 3: Add responsive behavior**

- Stable sidebar on desktop.
- Collapsible sidebar at 1024px.
- Drawer navigation at 390px and 320px.
- 44px mobile touch targets.
- `prefers-reduced-motion` support.

**Step 4: Run focused tests**

Run:

```powershell
cd frontend
pnpm vitest run src/layouts/WorkspaceLayout.spec.ts
pnpm build
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add frontend/src
git commit -m "feat: add responsive employee workspace"
```

### Task 10: Implement the long-term chat UI

**Files:**
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/chat.ts`
- Create: `frontend/src/features/chat/chat.store.ts`
- Create: `frontend/src/features/chat/ConversationList.vue`
- Create: `frontend/src/features/chat/MessageStream.vue`
- Create: `frontend/src/features/chat/MessageComposer.vue`
- Create: `frontend/src/features/chat/LearningStatus.vue`
- Modify: `frontend/src/views/ChatView.vue`
- Create: `frontend/src/features/chat/ChatView.spec.ts`

**Step 1: Write failing chat tests**

Test:

- create/select conversation;
- send text;
- attach image, Excel, file, and Etsy link;
- disable duplicate sends while an operation is active;
- render progress, final answer, failure, retry, and learning status;
- preserve messages after route change;
- keyboard focus returns to composer after send.

**Step 2: Implement API client and store**

Use typed request/response interfaces. Consume operation events over SSE, reconnect once on transient network failure, and fall back to polling final operation state if SSE closes unexpectedly.

**Step 3: Implement ChatGPT-like interaction**

Use a continuous message canvas, readable line length, fixed bottom composer, drag/drop overlay, compact tool status, and no card around every message. Keep internal machine events hidden.

**Step 4: Run focused tests**

Run:

```powershell
cd frontend
pnpm vitest run src/features/chat/ChatView.spec.ts
pnpm build
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add frontend/src/api frontend/src/features/chat frontend/src/views/ChatView.vue
git commit -m "feat: add persistent employee chat UI"
```

### Task 11: Implement the Excel automation UI

**Files:**
- Create: `frontend/src/api/excel.ts`
- Create: `frontend/src/features/excel/excel.store.ts`
- Create: `frontend/src/features/excel/ExcelDropzone.vue`
- Create: `frontend/src/features/excel/JobProgress.vue`
- Create: `frontend/src/features/excel/JobHistory.vue`
- Create: `frontend/src/features/excel/ResultDownload.vue`
- Modify: `frontend/src/views/ExcelView.vue`
- Create: `frontend/src/features/excel/ExcelView.spec.ts`

**Step 1: Write failing Excel UI tests**

Test:

- accept `.xlsx` and reject unsupported file types;
- upload starts one job;
- display queued/running/needs-review/completed/failed/cancelled;
- page refresh reloads persisted job state;
- completed job exposes one primary download action;
- failed job exposes a clear retry action;
- UI never asks the user to configure dynamic Excel headers.

**Step 2: Implement the upload-first workspace**

The empty state makes upload the dominant action. After upload, replace it with a continuous job surface containing file identity, employee status, progress, warnings, and download. Do not expose internal row parsing settings.

**Step 3: Add partial and error states**

Show employee-reported warnings without interpreting them in the browser. Preserve job history and allow selecting previous completed artifacts.

**Step 4: Run focused tests**

Run:

```powershell
cd frontend
pnpm vitest run src/features/excel/ExcelView.spec.ts
pnpm build
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add frontend/src/api/excel.ts frontend/src/features/excel frontend/src/views/ExcelView.vue
git commit -m "feat: add Excel automation UI"
```

### Task 12: Integrate, fix known bugs, and package the MVP

**Files:**
- Create: `backend/tests/test_mvp_flow.py`
- Create: `frontend/e2e/mvp-flow.spec.ts`
- Create: `frontend/playwright.config.ts`
- Create: `scripts/start.ps1`
- Modify: `README.md`
- Create: `docs/operations/mvp-runbook.md`

**Step 1: Write the backend critical-flow integration test**

With fake Hermes:

1. create a conversation;
2. send a training correction and persist a candidate;
3. upload the two-row workbook;
4. run two isolated row generations;
5. validate five outputs per row;
6. download a new workbook;
7. assert source hash unchanged;
8. export and reimport the employee package.

**Step 2: Write the browser critical-flow test**

Playwright must cover:

- open app;
- use long-term chat;
- navigate to Excel;
- upload workbook;
- observe progress;
- download result;
- reload and see history.

This is a functional end-to-end test, not a smoke or performance test.

**Step 3: Run all focused automated checks**

Run:

```powershell
cd backend
python -m pytest -q
cd ..\frontend
pnpm vitest run
pnpm build
pnpm playwright test e2e/mvp-flow.spec.ts
```

Expected: all pass with zero known failures.

**Step 4: Perform required UI screenshot verification**

Capture and inspect the real populated Excel and chat views at:

- 1440×900
- 1024×768
- 390×844
- 320×568

Fix overlaps, clipping, unreadable long Chinese labels, inaccessible focus, broken drawers, and blank states. This is visual QA, not a smoke or load test.

**Step 5: Verify real Hermes and real workbook paths**

Run the dedicated Profile verification script, then process a copy of the user-provided workbook. Confirm the source SHA-256 is unchanged and open the output to inspect the five target fields and preserved workbook layout. Do not test Etsy publishing or account login.

**Step 6: Document startup and recovery**

`README.md` and `docs/operations/mvp-runbook.md` must include:

- prerequisites;
- one-command startup;
- Profile verification;
- where data and artifacts are stored;
- how to retry failed jobs;
- how to export/import the employee;
- how to stop services;
- known MVP limitations.

**Step 7: Commit**

```powershell
git add backend/tests frontend/e2e frontend/playwright.config.ts scripts/start.ps1 README.md docs/operations
git commit -m "feat: deliver Etsy performance employee MVP"
```

## Final release checklist

- [ ] New `etsy-performance-us` Profile exists and did not clone another employee.
- [ ] Default Hermes hashes are unchanged.
- [ ] Real model call returns `PROFILE_READY`.
- [ ] Chat supports text, image, Excel, file, and Etsy link inputs.
- [ ] Uploaded workbook is handed to the employee without website-side semantic header configuration.
- [ ] One product fills one row; two products fill two isolated rows.
- [ ] Exactly five output fields are written.
- [ ] Original workbook hash is unchanged.
- [ ] Raw competitor text is unavailable to generation retrieval.
- [ ] Similarity guard blocks near-copy output.
- [ ] Knowledge promotion and rollback work.
- [ ] Migration export excludes secrets and imports successfully.
- [ ] Backend tests, frontend tests, production build, and critical Playwright flow pass.
- [ ] Required desktop and mobile screenshots have no blocking defects.
- [ ] `git status --short` is clean.

