"""Compatible RedLab channel metadata shared by the collector and dashboard."""

import json
import os
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, TypedDict


class ChannelMetadata(TypedDict):
    enabled: bool
    name: str


ChannelState = dict[str, bool | ChannelMetadata]


def default_redlab_channels(channel_keys: list[str]) -> ChannelState:
    return {key: True for key in channel_keys}


def is_redlab_selection_key(key: str, channel_keys: list[str]) -> bool:
    if key in channel_keys:
        return True
    if "|" not in key:
        return False
    device, channel = key.split("|", 1)
    return bool(device.strip()) and channel in channel_keys


def normalize_redlab_channels(
    payload: dict[str, Any], channel_keys: list[str], *, compact: bool = False,
) -> ChannelState:
    out = default_redlab_channels(channel_keys)
    for raw_key, value in payload.items():
        key = str(raw_key)
        if not is_redlab_selection_key(key, channel_keys):
            continue
        if isinstance(value, dict):
            enabled = bool(value.get("enabled", True))
            name = str(value.get("name", "")).strip()
            out[key] = enabled if compact and not name else {"enabled": enabled, "name": name}
        else:
            out[key] = bool(value)
    return out


def load_redlab_channels(state_path: Path, channel_keys: list[str]) -> ChannelState:
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_redlab_channels(channel_keys)
    if not isinstance(raw, dict):
        return default_redlab_channels(channel_keys)
    return normalize_redlab_channels(raw, channel_keys)


def save_redlab_channels(
    state_path: Path, channel_keys: list[str], payload: dict[str, Any],
) -> ChannelState:
    out = normalize_redlab_channels(payload, channel_keys, compact=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(state_path.stat().st_mode)
    except FileNotFoundError:
        mode = 0o644
    temporary_path = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=state_path.parent,
                                prefix=f".{state_path.name}.", suffix=".tmp", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(json.dumps(out, indent=2, sort_keys=True))
            handle.flush()
            os.fchmod(handle.fileno(), mode)
        os.replace(temporary_path, state_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return out
