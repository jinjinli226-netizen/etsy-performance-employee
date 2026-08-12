"""Inspect Hermes YAML without ever returning configuration values."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml


SENSITIVE_SECTIONS = {
    "gateway",
    "channel",
    "channels",
    "message",
    "messaging",
    "telegram",
    "discord",
    "slack",
    "whatsapp",
    "weixin",
    "wechat",
    "signal",
}


def is_active(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "disabled", "false", "null", "off", "~"}
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return True


def inspect(node: Any, path: tuple[str, ...], forbidden: list[str]) -> bool:
    model_api_key_present = False
    if (
        path
        and any(part in SENSITIVE_SECTIONS for part in path)
        and not isinstance(node, (dict, list))
        and is_active(node)
    ):
        forbidden.append(".".join(path))
    if isinstance(node, dict):
        for raw_key, value in node.items():
            key = str(raw_key).lower()
            child_path = (*path, key)
            if child_path == ("model", "api_key") and is_active(value):
                model_api_key_present = True
            if inspect(value, child_path, forbidden):
                model_api_key_present = True
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if inspect(value, (*path, str(index)), forbidden):
                model_api_key_present = True
    return model_api_key_present


def main() -> int:
    if len(sys.argv) != 2:
        print("Configuration inspection failed.", file=sys.stderr)
        return 2
    try:
        loaded = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ValueError("root must be a mapping")
        forbidden: list[str] = []
        key_present = inspect(loaded, (), forbidden)
        print(
            json.dumps(
                {
                    "model_api_key_present": key_present,
                    "forbidden_paths": sorted(set(forbidden)),
                },
                separators=(",", ":"),
            )
        )
        return 0
    except Exception:
        # Never include parser details because YAML errors can quote secret values.
        print("Configuration inspection failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
