from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.knowledge.schemas import CandidatePage, CandidateStatusRead, EvidenceInput, KnowledgeStatus, PatternPage, PatternTransitionRead
from app.knowledge.service import KnowledgeCapacityError, KnowledgeConflictError, KnowledgeNotFoundError, KnowledgeService, KnowledgeValidationError


router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def require_evidence_capability() -> None:
    raise HTTPException(403, "Trusted employee evidence capability required")


@router.post("/evidence", include_in_schema=False)
def ingest_evidence(
    payload: EvidenceInput,
    request: Request,
    _capability: None = Depends(require_evidence_capability),
):
    try:
        record = service(request).ingest_evidence(payload)
    except KnowledgeCapacityError as exc:
        raise HTTPException(507, "Evidence guard capacity reached") from exc
    return {"id": record.public_id, "source_timestamp": record.source_timestamp}


def service(request: Request) -> KnowledgeService:
    return request.app.state.knowledge_service


@router.get("/capacity")
def knowledge_capacity(request: Request):
    return service(request).capacity_status()


@router.get("", response_model=PatternPage)
def list_active_patterns(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    kind: str | None = Query(None, min_length=1, max_length=127),
):
    return service(request).list_active(limit=limit, offset=offset, kind=kind)


@router.get("/candidates/status", response_model=list[CandidateStatusRead])
def candidate_statuses(
    request: Request,
    trace_id: str = Query(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
):
    return service(request).candidate_statuses(trace_id)


@router.get("/candidates", response_model=CandidatePage)
def list_candidates(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: KnowledgeStatus | None = None,
):
    return service(request).list_candidates(limit=limit, offset=offset, status=status)


def _transition(service_: KnowledgeService, pattern_id: int) -> PatternTransitionRead:
    # Activation endpoints only need the safe abstract response. Internal numeric id is
    # retained as the resource id used by rollback.
    with service_.session_factory() as session:
        from app.db.models import KnowledgePattern, RuleVersion
        from sqlalchemy import select
        stored = session.get(KnowledgePattern, pattern_id)
        if stored is None:
            raise KnowledgeNotFoundError
        version = session.scalar(select(RuleVersion).where(RuleVersion.pattern_id == pattern_id, RuleVersion.status == KnowledgeStatus.ACTIVE).order_by(RuleVersion.sequence.desc()))
        if version is None:
            raise KnowledgeConflictError("pattern has no active version")
        return PatternTransitionRead(id=stored.id, public_id=stored.public_id or str(stored.id), kind=stored.kind or stored.name, abstract=stored.abstract_summary or "", rule_version=version.version, status=stored.status)


@router.post("/candidates/{candidate_id}/approve", response_model=PatternTransitionRead)
def approve_candidate(candidate_id: int, request: Request):
    active = service(request)
    try:
        pattern = active.approve_candidate(candidate_id, actor="owner")
        return _transition(active, pattern.id)
    except KnowledgeNotFoundError as exc:
        raise HTTPException(404, "Knowledge candidate not found") from exc
    except (KnowledgeConflictError, KnowledgeValidationError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/candidates/{candidate_id}/reject")
def reject_candidate(candidate_id: int, request: Request):
    try:
        candidate = service(request).reject_candidate(candidate_id, actor="owner")
        return {"id": candidate.id, "status": candidate.status}
    except KnowledgeNotFoundError as exc:
        raise HTTPException(404, "Knowledge candidate not found") from exc
    except KnowledgeConflictError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/patterns/{pattern_id}/rollback", response_model=PatternTransitionRead)
def rollback_pattern(pattern_id: int, request: Request, expected_rule_version: str | None = Query(None, min_length=1, max_length=127)):
    try:
        return service(request).rollback_pattern(pattern_id, actor="owner", expected_rule_version=expected_rule_version)
    except KnowledgeNotFoundError as exc:
        raise HTTPException(404, "Knowledge pattern not found") from exc
    except KnowledgeConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
