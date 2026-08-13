from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from app.chat.schemas import (
    AttachmentRead,
    ChatSendRequest,
    ConversationCreate,
    ConversationPage,
    ConversationSummary,
    MessageRead,
    OperationAccepted,
)
from app.chat.service import (
    AttachmentScopeError,
    ChatService,
    ConversationBusyError,
    ConversationNotFoundError,
)
from app.db.models import Attachment, Conversation
from app.employee.adapter import EmployeeUnavailableError

router = APIRouter(prefix="/api")

ALLOWED_MEDIA_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
    "text/plain",
    "application/pdf",
    "application/zip",
    "application/octet-stream",
}
BLOCKED_SUFFIXES = {".bat", ".cmd", ".com", ".exe", ".js", ".msi", ".ps1", ".scr"}


def service(request: Request) -> ChatService:
    return request.app.state.chat_service


@router.post("/conversations", response_model=ConversationSummary, status_code=201)
def create_conversation(payload: ConversationCreate, request: Request):
    return service(request).create_conversation(payload.title)


@router.get("/conversations", response_model=ConversationPage)
def list_conversations(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    items, total = service(request).list_conversations(limit, offset)
    return ConversationPage(
        items=[ConversationSummary.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageRead])
def list_messages(conversation_id: int, request: Request):
    try:
        return service(request).list_messages(conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=OperationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_message(conversation_id: int, payload: ChatSendRequest, request: Request):
    try:
        operation_id = await service(request).start_send(
            conversation_id, payload.content, payload.attachment_ids, learning_mode=payload.learning_mode
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    except AttachmentScopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConversationBusyError as exc:
        raise HTTPException(status_code=409, detail="Conversation is already processing") from exc
    except EmployeeUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return OperationAccepted(operation_id=operation_id, status="running")


def _safe_filename(filename: str | None) -> str:
    name = Path((filename or "attachment").replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return (cleaned or "attachment")[:255]


@router.post("/attachments", response_model=AttachmentRead, status_code=201)
async def upload_attachment(
    request: Request,
    conversation_id: int = Form(...),
    file: UploadFile = File(...),
):
    settings = request.app.state.settings
    media_type = (file.content_type or "application/octet-stream").lower()
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported attachment type")

    filename = _safe_filename(file.filename)
    suffix = Path(filename).suffix.lower()[:16]
    if suffix in BLOCKED_SUFFIXES:
        raise HTTPException(status_code=422, detail="Unsupported attachment type")
    storage_dir = (settings.data_dir / "attachments" / str(conversation_id)).resolve()
    attachment_root = (settings.data_dir / "attachments").resolve()
    if not storage_dir.is_relative_to(attachment_root):
        raise HTTPException(status_code=422, detail="Invalid storage path")

    with request.app.state.session_factory() as session:
        if session.get(Conversation, conversation_id) is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

    content = await file.read(settings.max_attachment_bytes + 1)
    if len(content) > settings.max_attachment_bytes:
        raise HTTPException(status_code=422, detail="Attachment is too large")
    if not content:
        raise HTTPException(status_code=422, detail="Attachment is empty")
    storage_dir.mkdir(parents=True, exist_ok=True)
    destination = storage_dir / f"{uuid4().hex}{suffix}"
    destination.write_bytes(content)

    try:
        with request.app.state.session_factory() as session:
            attachment = Attachment(
                conversation_id=conversation_id,
                filename=filename,
                path=str(destination),
                media_type=media_type,
            )
            session.add(attachment)
            session.commit()
            return attachment
    except Exception:
        destination.unlink(missing_ok=True)
        raise


@router.get("/events/{operation_id}")
async def stream_events(operation_id: str, request: Request):
    chat_service = service(request)
    try:
        generator = chat_service.operation_events(operation_id)
        first = await anext(generator)
    except (KeyError, StopAsyncIteration) as exc:
        raise HTTPException(status_code=404, detail="Operation not found") from exc

    async def generate():
        yield f"data: {json.dumps(first, separators=(',', ':'))}\n\n"
        async for event in generator:
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
