from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.api.excel_jobs import router as excel_jobs_router
from app.chat.service import ChatService
from app.core.config import Settings, get_settings
from app.db.init_db import init_db
from app.db.session import create_engine_for_url, create_session_factory
from app.employee.adapter import HermesAdapter, SubprocessHermesAdapter
from app.excel_jobs.runner import ExcelRunner, SubprocessExcelRunner
from app.excel_jobs.service import ExcelJobService


def create_app(
    *,
    settings: Settings | None = None,
    employee: HermesAdapter | None = None,
    excel_runner: ExcelRunner | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime_settings.ensure_runtime_dirs()
        engine = create_engine_for_url(runtime_settings.resolved_database_url)
        init_db(engine)
        factory = create_session_factory(engine)
        runtime_employee = employee or SubprocessHermesAdapter(
            executable=runtime_settings.hermes_executable,
            profile=runtime_settings.hermes_profile,
            timeout_seconds=runtime_settings.hermes_timeout_seconds,
            data_root=runtime_settings.data_dir,
            max_turns=runtime_settings.hermes_max_turns,
        )
        app.state.settings = runtime_settings
        app.state.engine = engine
        app.state.session_factory = factory
        app.state.chat_service = ChatService(factory, runtime_employee)
        app.state.chat_service.reconcile_interrupted_operations()
        runtime_excel_runner = excel_runner or SubprocessExcelRunner(
            repository_root=Path(__file__).resolve().parents[2],
            max_event_bytes=runtime_settings.max_worker_event_bytes,
            cancel_timeout_seconds=runtime_settings.excel_cancel_timeout_seconds,
            worker_timeout_seconds=runtime_settings.excel_worker_timeout_seconds,
        )
        app.state.excel_job_service = ExcelJobService(factory, runtime_excel_runner, runtime_settings)
        app.state.excel_job_service.reconcile_interrupted_jobs()
        try:
            yield
        finally:
            tasks = list(app.state.chat_service._tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                import asyncio

                await asyncio.gather(*tasks, return_exceptions=True)
            await app.state.excel_job_service.shutdown()
            engine.dispose()

    application = FastAPI(title="Etsy Performance Employee", lifespan=lifespan)

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(chat_router)
    application.include_router(excel_jobs_router)
    return application


app = create_app()
