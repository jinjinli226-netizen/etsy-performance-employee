from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.employee.adapter import (
    EmployeeUnavailableError,
    HermesCancelledError,
    HermesMalformedReplyError,
    HermesProcessError,
    HermesTimeoutError,
    SubprocessHermesAdapter,
    resolve_hermes_profiles_root,
)
from tests.fakes.fake_hermes import FakeProcess


@pytest.fixture
def anyio_backend():
    return "asyncio"


def adapter(tmp_path: Path, *, timeout: float = 1) -> SubprocessHermesAdapter:
    return SubprocessHermesAdapter(
        executable="hermes",
        profile="etsy-performance-us",
        timeout_seconds=timeout,
        data_root=tmp_path,
        max_turns=7,
        profiles_root=tmp_path / "profiles",
    )


def test_check_available_rejects_missing_executable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda executable: None)

    with pytest.raises(EmployeeUnavailableError, match="unavailable"):
        adapter(tmp_path).check_available()


def test_check_available_rejects_missing_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda executable: str(tmp_path / "hermes.exe"))

    with pytest.raises(EmployeeUnavailableError, match="profile"):
        adapter(tmp_path).check_available()


def test_windows_profiles_root_uses_local_app_data_and_existing_profile(
    tmp_path, monkeypatch
) -> None:
    local_app_data = tmp_path / "LocalAppData"
    expected_root = local_app_data / "hermes" / "profiles"
    (expected_root / "etsy-performance-us").mkdir(parents=True)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr("shutil.which", lambda executable: str(tmp_path / "hermes.exe"))

    root = resolve_hermes_profiles_root(platform_name="windows")
    real = SubprocessHermesAdapter(
        executable="hermes",
        profile="etsy-performance-us",
        data_root=tmp_path / "data",
        profiles_root=root,
    )

    assert root == expected_root.resolve()
    real.check_available()


def test_windows_profiles_root_missing_profile_is_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.setattr("shutil.which", lambda executable: str(tmp_path / "hermes.exe"))
    real = SubprocessHermesAdapter(
        executable="hermes",
        profile="etsy-performance-us",
        data_root=tmp_path / "data",
        profiles_root=resolve_hermes_profiles_root(platform_name="windows"),
    )

    with pytest.raises(EmployeeUnavailableError, match="profile"):
        real.check_available()


def test_explicit_hermes_home_override_wins_on_windows(tmp_path, monkeypatch) -> None:
    override = tmp_path / "override-home"
    monkeypatch.setenv("HERMES_HOME", str(override))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "ignored-local"))

    assert resolve_hermes_profiles_root(platform_name="windows") == (
        override / "profiles"
    ).resolve()


@pytest.mark.anyio
async def test_builds_exact_noninteractive_command_with_resume_and_valid_image(
    tmp_path, monkeypatch
) -> None:
    image = tmp_path / "attachments" / "photo.png"
    image.parent.mkdir()
    image.write_bytes(b"png")
    captured: dict[str, tuple[str, ...]] = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = tuple(sorted(kwargs))
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    reply = await adapter(tmp_path).send(
        "Review this", session_id="session-old", image_path=image, source="app"
    )

    assert captured["args"] == (
        "hermes",
        "-p",
        "etsy-performance-us",
        "chat",
        "-Q",
        "--source",
        "app",
        "--max-turns",
        "7",
        "--pass-session-id",
        "-q",
        "Review this",
        "--resume",
        "session-old",
        "--image",
        str(image.resolve()),
    )
    assert "--yolo" not in captured["args"]
    assert reply.text == "Fake employee reply"
    assert reply.session_id == "fake-session-1"


@pytest.mark.anyio
async def test_omits_resume_and_image_when_not_supplied(tmp_path, monkeypatch) -> None:
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await adapter(tmp_path).send("Hello", session_id=None, image_path=None, source="tool")

    assert "--resume" not in captured["args"]
    assert "--image" not in captured["args"]
    assert captured["args"][0:4] == ("hermes", "-p", "etsy-performance-us", "chat")


@pytest.mark.anyio
async def test_rejects_image_outside_data_root_before_starting_process(tmp_path, monkeypatch) -> None:
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"png")

    async def should_not_run(*args, **kwargs):
        raise AssertionError("process must not start")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", should_not_run)

    with pytest.raises(ValueError, match="image path"):
        await adapter(tmp_path).send("Hello", None, outside, "app")


@pytest.mark.anyio
async def test_nonzero_exit_raises_sanitized_typed_error(tmp_path, monkeypatch) -> None:
    secret = "sk-secret-must-not-leak"

    async def fake_exec(*args, **kwargs):
        return FakeProcess(returncode=2, stderr=f"provider failed {secret}".encode())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(HermesProcessError) as raised:
        await adapter(tmp_path).send("Hello", None, None, "app")
    assert secret not in str(raised.value)
    assert "provider failed" not in str(raised.value)


@pytest.mark.anyio
async def test_missing_executable_raises_typed_unavailable_error(tmp_path, monkeypatch) -> None:
    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError("secret local executable path")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(HermesProcessError, match="unavailable"):
        await adapter(tmp_path).send("Hello", None, None, "app")


@pytest.mark.anyio
async def test_blank_or_sessionless_output_is_malformed(tmp_path, monkeypatch) -> None:
    processes = [
        FakeProcess(stdout=b"", stderr=b"session_id: session-1\n"),
        FakeProcess(stdout=b"reply", stderr=b"unrelated warning\n"),
    ]

    async def fake_exec(*args, **kwargs):
        return processes.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(HermesMalformedReplyError):
        await adapter(tmp_path).send("Hello", None, None, "app")
    with pytest.raises(HermesMalformedReplyError):
        await adapter(tmp_path).send("Hello", None, None, "app")


@pytest.mark.anyio
async def test_timeout_terminates_child_and_raises_typed_error(tmp_path, monkeypatch) -> None:
    process = FakeProcess(wait_forever=True)

    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(HermesTimeoutError):
        await adapter(tmp_path, timeout=0.01).send("Hello", None, None, "app")
    assert process.terminated


@pytest.mark.anyio
async def test_cancellation_terminates_child_and_propagates_typed_cancel(tmp_path, monkeypatch) -> None:
    process = FakeProcess(wait_forever=True)

    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    task = asyncio.create_task(adapter(tmp_path).send("Hello", None, None, "app"))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(HermesCancelledError):
        await task
    assert process.terminated
