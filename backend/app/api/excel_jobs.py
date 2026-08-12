from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse

from app.excel_jobs.schemas import ExcelJobPage, ExcelJobRead
from app.excel_jobs.service import ExcelJobService, JobConflictError, JobNotFoundError
from app.excel_jobs.storage import StorageError, store_upload


router = APIRouter(prefix="/api/excel-jobs", tags=["excel-jobs"])


def service(request: Request) -> ExcelJobService:
    return request.app.state.excel_job_service


def view_payload(view) -> ExcelJobRead:
    return ExcelJobRead.model_validate(
        {
            "id": view.id,
            "source_filename": view.source_filename,
            "source_sha256": view.source_sha256,
            "source_size_bytes": view.source_size_bytes,
            "status": view.status,
            "progress_percent": view.progress_percent,
            "error": view.error,
            "created_at": view.created_at,
            "updated_at": view.updated_at,
            "artifact": view.artifact.__dict__ if view.artifact else None,
        }
    )


@router.post("", response_model=ExcelJobRead, status_code=status.HTTP_202_ACCEPTED)
async def create_excel_job(request: Request, file: UploadFile = File(...)):
    public_id = uuid4()
    settings = request.app.state.settings
    try:
        stored = await store_upload(
            file,
            root=settings.data_dir / "excel-jobs",
            public_id=public_id,
            max_bytes=settings.max_excel_upload_bytes,
        )
        job = service(request).create_job(stored, file.filename or "workbook.xlsx")
        await service(request).start_job(str(public_id))
        return view_payload(job)
    except StorageError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("", response_model=ExcelJobPage)
def list_excel_jobs(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    items, total = service(request).list_jobs(limit, offset)
    return ExcelJobPage(items=[view_payload(item) for item in items], total=total, limit=limit, offset=offset)


@router.get("/{public_id}", response_model=ExcelJobRead)
def get_excel_job(public_id: UUID, request: Request):
    try:
        return view_payload(service(request).get_job(str(public_id)))
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Excel job not found") from exc


@router.get("/{public_id}/events")
async def excel_job_events(public_id: UUID, request: Request):
    try:
        service(request).get_job(str(public_id))
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Excel job not found") from exc
    raw_last = request.headers.get("last-event-id", "0")
    try:
        last_id = int(raw_last)
        if last_id < 0:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid Last-Event-ID") from exc

    async def stream():
        cursor = last_id
        wakeup = service(request).wakeup(str(public_id))
        while True:
            wakeup.clear()
            events, terminal = await asyncio.to_thread(
                service(request).events_after, str(public_id), cursor
            )
            for event in events:
                cursor = event.id
                payload = {"type": event.event_type, **event.payload}
                yield f"id: {event.id}\nevent: {event.event_type}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
            if events:
                continue
            if terminal:
                break
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=15)
            except TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{public_id}/cancel", response_model=ExcelJobRead)
async def cancel_excel_job(public_id: UUID, request: Request):
    try:
        return view_payload(await service(request).cancel(str(public_id)))
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Excel job not found") from exc


@router.get("/{public_id}/download")
def download_excel_job(public_id: UUID, request: Request):
    try:
        path, filename = service(request).download(str(public_id))
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=Path(filename).name,
        )
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Excel job not found") from exc
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
