from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.chat.service import ChatService
from app.core.config import Settings, get_settings
from app.db.init_db import init_db
from app.db.session import create_engine_for_url, create_session_factory
from app.employee.adapter import HermesAdapter, SubprocessHermesAdapter


def create_app(
    *,
    settings: Settings | None = None,
    employee: HermesAdapter | None = None,
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
        try:
            yield
        finally:
            tasks = list(app.state.chat_service._tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                import asyncio

                await asyncio.gather(*tasks, return_exceptions=True)
            engine.dispose()

    application = FastAPI(title="Etsy Performance Employee", lifespan=lifespan)

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(chat_router)
    return application


app = create_app()
