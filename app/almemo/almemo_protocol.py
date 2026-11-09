"""Pure parsing and command helpers for ALMEMO V6/V7."""

import re
from dataclasses import dataclass


RECORD_START_RE = re.compile(r"^\d{2}:\d{2}:\d{2}(?:\.\d{2})?\b")
CHANNEL_VALUE_RE = re.compile(
    r"(\d{2}):\s*([<>]?[+-]?\d+(?:\.\d*)?)\s+(\S+)(?:\s+(.*?))?(?=(?:\s+\d{2}:)|$)"
)
CHANNEL_VALUE_V7_RE = re.compile(
    r"(?:^|;)(\d+\.\d+);([<>]?[+-]?\d+(?:\.\d*)?);?([^;\s]+)?(?=(?:;|$))"
)
DEVICE_VERSION_RE = re.compile(r"^[A-Z]?\d{4}-[A-Z0-9]+(?:\s+\S.*)?$")

def _normalize_line_text(line: str) -> str:
    return line.replace("\r", "").replace("\n", "").replace("\x03", "").strip()


def _clean_fast_live_line(line: str) -> str:
    return _normalize_line_text(line)


def _record_start(line: str) -> bool:
    return bool(RECORD_START_RE.match(line.strip()))


def _normalize_live_cycle(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 6:
        return "000001"
    return digits


def _live_setup_commands(version: str, cycle: str) -> tuple[str, ...]:
    if version == "V7":
        return ("C11", "f5 k5", "G00", f"Z{cycle}", "S2")
    # Some V6 ALMEMO 2490 units acknowledge S2 but do not emit printable
    # records until the first measurement channel is selected again.
    return ("C11", "f5 k-5", "f5 k-4", "f5 k-2", "G00", f"Z{cycle}", "S2", "M00")


def _fast_live_rate_command(rate: str) -> str:
    return "f5 k0" if rate == "2.5" else "f5 k1"


def _decode_eol(raw: str) -> str:
    return (
        raw.replace("\\r", "\r")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
    )


def _extract_device_version(lines: list[str]) -> str | None:
    for raw_line in lines:
        line = _normalize_line_text(raw_line)
        if not line or line in {"t0", "G00", "C11"}:
            continue
        upper_line = line.upper()
        if "ALMEMO" in upper_line:
            return line
        if DEVICE_VERSION_RE.match(line):
            return line
    return None


@dataclass(frozen=True)
class Measurement:
    channel: str
    value: float
    unit: str
    sensor: str


def parse_measurements(record: str, version: str) -> list[Measurement]:
    text = " ".join(record.strip().split())
    if not text:
        return []
    payload = RECORD_START_RE.sub("", text, count=1).strip().lstrip("|").strip()
    payload = payload.replace("|", " ")
    regex = CHANNEL_VALUE_V7_RE if version == "V7" else CHANNEL_VALUE_RE
    measurements = []
    for match in regex.finditer(payload):
        try:
            value = float(match.group(2).lstrip("<>"))
        except ValueError:
            continue
        sensor = (match.group(4) or "").strip() if version == "V6" and len(match.groups()) >= 4 else ""
        measurements.append(Measurement(match.group(1), value, (match.group(3) or "").strip(), sensor))
    return measurements
