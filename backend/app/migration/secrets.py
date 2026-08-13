from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


class SensitiveDataError(ValueError):
    pass


_KEY = re.compile(r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|cookie|password|passwd|private[_-]?key|client[_-]?secret|connection[_-]?(?:uri|string))", re.I)
_VALUE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[pbar]-[A-Za-z0-9-]{16,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{30,}|Bearer\s+[A-Za-z0-9._~-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:@/]+:[^\s@/]+@)",
    re.I,
)
_ASSIGNMENT = re.compile(r"(?:api[ _-]?key|apikey|token|secret|password|cookie(?:value)?|session|auth(?:orization)?)\s*(?:(?:is|[:=])\s*)?[^\s,;]{8,}", re.I)
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


def _entropy(value: str) -> float:
    counts = Counter(value)
    return -sum((count / len(value)) * math.log2(count / len(value)) for count in counts.values())


def scan_for_secrets(value: Any, *, key: str = "") -> None:
    if key and _KEY.search(key):
        raise SensitiveDataError("sensitive key name detected")
    if isinstance(value, dict):
        for child_key, child in value.items():
            scan_for_secrets(child, key=str(child_key))
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            scan_for_secrets(child)
        return
    if not isinstance(value, str):
        return
    if _VALUE.search(value) or _ASSIGNMENT.search(value) or _JWT.search(value):
        raise SensitiveDataError("credential-like value detected")
    # Only treat high entropy as sensitive in an explicitly credential-bearing context.
    if key and re.search(r"(?:credential|auth|secret|token)", key, re.I) and len(value) >= 24 and _entropy(value) >= 4.2:
        raise SensitiveDataError("high entropy credential-like value detected")
