"""Explicitly test-only app wiring used by Playwright; never imported by production."""

import os
import shutil
import stat
from contextlib import asynccontextmanager
from pathlib import Path

import app.migration.capability as capability
from fastapi import HTTPException
from app.core.config import Settings
from app.main import create_app
from tests.fakes.mvp_runtime import EmployeeSkillRunner, StaticHermes


if os.environ.get("ETSY_EMPLOYEE_TEST_MODE") != "1":
    raise RuntimeError("The Playwright fake app requires explicit test mode.")
settings = Settings()
data_dir = settings.data_dir.resolve()
expected_root = Path(__file__).resolve().parents[2] / ".e2e-data"
if not data_dir.name.startswith("run-") or data_dir.parent != expected_root:
    raise RuntimeError("The Playwright data directory is not the owned test directory.")

# Windows ACL hardening is covered by backend tests. Keeping the capability file
# normally inherited here lets the owning test process remove its private run
# directory after Uvicorn closes every database and artifact handle.
capability._lock_windows_acl = lambda path: None
app = create_app(settings=settings, employee=StaticHermes(), excel_runner=EmployeeSkillRunner())
production_lifespan = app.router.lifespan_context


def remove_readonly_test_file(function, path, _error_info):
    candidate = Path(path).resolve()
    if not candidate.is_relative_to(data_dir):
        raise RuntimeError("Refusing to clean a path outside the owned E2E directory")
    os.chmod(candidate, stat.S_IWRITE)
    function(candidate)


@app.post("/__e2e__/shutdown", include_in_schema=False)
async def shutdown_e2e_server():
    server = getattr(app.state, "e2e_server", None)
    if server is None:
        raise HTTPException(status_code=503, detail="E2E server handle is unavailable")
    server.should_exit = True
    return {"status": "stopping"}


@asynccontextmanager
async def e2e_lifespan(application):
    try:
        async with production_lifespan(application):
            yield
    finally:
        shutil.rmtree(data_dir, ignore_errors=False, onerror=remove_readonly_test_file)


app.router.lifespan_context = e2e_lifespan
