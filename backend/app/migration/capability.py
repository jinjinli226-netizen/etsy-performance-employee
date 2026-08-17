from __future__ import annotations

import hashlib
import os
import secrets
import stat
import subprocess
import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OwnedCapability:
    path: Path
    token: str
    digest: str


def _windows_powershell_environment() -> dict[str, str]:
    environment = dict(os.environ)
    casefolded = {key.casefold(): value for key, value in environment.items()}
    system_root = casefolded.get("systemroot")
    program_files = casefolded.get("programfiles")
    if not system_root or not program_files:
        raise RuntimeError("Windows PowerShell module roots are unavailable")
    for key in tuple(environment):
        if key.casefold() == "psmodulepath":
            del environment[key]
    environment["PSModulePath"] = os.pathsep.join(
        (
            str(Path(program_files) / "WindowsPowerShell" / "Modules"),
            str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "Modules"),
        )
    )
    return environment


def _lock_windows_acl(path: Path) -> None:
    if os.name != "nt":
        return
    identity = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        check=True, capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    rows = list(csv.reader(io.StringIO(identity)))
    if len(rows) != 1 or len(rows[0]) < 2 or not rows[0][1].startswith("S-"):
        raise RuntimeError("could not resolve the current Windows SID")
    sid = rows[0][1]
    for principal in ("*S-1-1-0", "*S-1-5-11", "*S-1-5-32-545", "*S-1-5-32-544"):
        subprocess.run(
            ["icacls.exe", str(path), "/remove:g", principal],
            check=False, capture_output=True, text=True, timeout=5,
        )
    subprocess.run(
        ["icacls.exe", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(R,W)"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    escaped = str(path).replace("'", "''")
    acl_script = (
        f"$acl=Get-Acl -LiteralPath '{escaped}'; "
        "$allows=@($acl.Access|Where-Object AccessControlType -eq 'Allow'|ForEach-Object "
        "{$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value}); "
        "[pscustomobject]@{protected=$acl.AreAccessRulesProtected;allows=$allows}|ConvertTo-Json -Compress"
    )
    snapshot = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", acl_script], check=True,
        capture_output=True, text=True, timeout=5, env=_windows_powershell_environment(),
    ).stdout
    _validate_windows_acl_snapshot(json.loads(snapshot), sid)


def _validate_windows_acl_snapshot(snapshot: dict[str, object], current_sid: str) -> None:
    raw = snapshot.get("allows", [])
    allows = {raw} if isinstance(raw, str) else set(raw) if isinstance(raw, list) else set()
    if snapshot.get("protected") is not True or current_sid not in allows or not allows.issubset({current_sid, "S-1-5-18"}):
        raise RuntimeError("migration capability ACL verification failed")


def create_capability_file(data_dir: Path, explicit_token: str | None = None) -> OwnedCapability:
    data_dir = data_dir.resolve()
    runtime = data_dir / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    path = runtime / "migration-capability"
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or path.parent.resolve() != runtime.resolve():
            raise RuntimeError("existing migration capability file is unsafe")
        _lock_windows_acl(path)
        try:
            old_token = path.read_text(encoding="ascii")
        except (OSError, UnicodeError) as error:
            raise RuntimeError("existing migration capability file is unsafe") from error
        if not 32 <= len(old_token) <= 256:
            raise RuntimeError("existing migration capability file is unsafe")
        path.unlink()
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
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeError("migration capability path is unsafe")
        _lock_windows_acl(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
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
