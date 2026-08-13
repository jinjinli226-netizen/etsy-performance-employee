from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

MAX_GUARD_BYTES = 8 * 1024 * 1024
MAX_GUARD_RECORDS = 500
MAX_SHINGLES_PER_RECORD = 30_000
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


class GuardValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PortableGuard:
    public_id: str
    shingles: tuple[str, ...]
    source_timestamp: datetime | None
    threshold: float
    content_hash: str | None
    snapshot_hash: str | None


def validate_shingles(values: Sequence[str]) -> tuple[str, ...]:
    if not values or len(values) > MAX_SHINGLES_PER_RECORD:
        raise GuardValidationError("evidence guard shingles are empty or oversized")
    if list(values) != sorted(values) or len(values) != len(set(values)):
        raise GuardValidationError("evidence guard shingles must be unique and sorted")
    if any(not isinstance(value, str) or not _FINGERPRINT.fullmatch(value) for value in values):
        raise GuardValidationError("evidence guard shingle is not canonical SHA-256")
    return tuple(values)


def merge_guards(records: Iterable[PortableGuard]) -> tuple[list[PortableGuard], float]:
    merged: dict[str, PortableGuard] = {}
    for item in records:
        canonical = PortableGuard(
            public_id=item.public_id,
            shingles=validate_shingles(item.shingles),
            source_timestamp=item.source_timestamp,
            threshold=item.threshold,
            content_hash=item.content_hash,
            snapshot_hash=item.snapshot_hash,
        )
        previous = merged.get(item.public_id)
        if previous is not None:
            if previous.shingles != canonical.shingles:
                raise GuardValidationError("conflicting evidence guard fingerprints")
            canonical = PortableGuard(
                item.public_id,
                canonical.shingles,
                previous.source_timestamp or canonical.source_timestamp,
                min(previous.threshold, canonical.threshold),
                previous.content_hash or canonical.content_hash,
                previous.snapshot_hash or canonical.snapshot_hash,
            )
        merged[item.public_id] = canonical
    output = [merged[key] for key in sorted(merged)]
    if len(output) > MAX_GUARD_RECORDS:
        raise GuardValidationError("evidence guard record capacity exceeded")
    threshold = min((item.threshold for item in output), default=.72)
    return output, threshold


def validate_portable_guard_size(records: Sequence[dict[str, object]], threshold: float, *, max_bytes: int = MAX_GUARD_BYTES) -> None:
    envelope = {"schema_version": 1, "threshold": threshold, "records": records}
    encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_bytes:
        raise GuardValidationError("evidence guard encoded capacity exceeded")
