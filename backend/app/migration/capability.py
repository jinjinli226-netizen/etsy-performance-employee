from __future__ import annotations

import hashlib
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OwnedCapability:
    path: Path
    token: str
    digest: str


def create_capability_file(data_dir: Path, explicit_token: str | None = None) -> OwnedCapability:
    runtime = data_dir / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    path = runtime / "migration-capability"
    if path.exists() or path.is_symlink():
        raise RuntimeError("migration capability file already exists")
    token = explicit_token or secrets.token_urlsafe(32)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    try:
        os.write(descriptor, token.encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return OwnedCapability(path.resolve(), token, hashlib.sha256(token.encode("ascii")).hexdigest())


def remove_owned_capability_file(capability: OwnedCapability) -> None:
    try:
        if capability.path.is_symlink() or not capability.path.is_file():
            return
        token = capability.path.read_text(encoding="ascii")
        if hashlib.sha256(token.encode("ascii")).hexdigest() == capability.digest:
            capability.path.unlink()
    except OSError:
        return
