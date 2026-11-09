from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib import request


def decode_transport_payload(payload_text: str) -> list[str]:
    text = payload_text.strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return [line.strip() for line in payload_text.splitlines() if line.strip()]
    return extract_lines_from_payload(payload)


def extract_lines_from_payload(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        lines = payload.get("lines")
        line = payload.get("line")
        if isinstance(lines, list):
            return [str(item).strip() for item in lines if str(item).strip()]
        if isinstance(line, str) and line.strip():
            return [line.strip()]
        return []
    if isinstance(payload, list):
        return [str(item).strip() for item in payload if str(item).strip()]
    if isinstance(payload, str):
        return [line.strip() for line in payload.splitlines() if line.strip()]
    return []


def load_transport_lines(source: str, *, file_path: str, http_url: str, timeout_sec: float) -> list[str]:
    mode = source.strip().lower()
    if mode in {"", "off", "disabled", "none"}:
        return []
    if mode == "file":
        content = Path(file_path).read_text(encoding="utf-8")
        return decode_transport_payload(content)
    if mode == "http":
        with request.urlopen(http_url, timeout=timeout_sec) as response:
            payload = response.read().decode("utf-8", errors="replace")
        return decode_transport_payload(payload)
    raise ValueError(f"unsupported transport source {source}")
