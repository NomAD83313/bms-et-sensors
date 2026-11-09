from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


_PREFIX_RE = re.compile(r"^\s*(ThreadDiag|ThreadDiagParent|ThreadDiagNeighbor)\s*:\s*(.*)\s*$")
_KEY_VALUE_RE = re.compile(r"([A-Za-z0-9_]+)=([^\s]+)")
_HEX16_RE = re.compile(r"^[0-9a-f]{16}$")
_RLOC16_RE = re.compile(r"^0x[0-9a-f]{1,4}$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")


@dataclass(frozen=True)
class ThreadDiagNodeRecord:
    kind: str
    schema: int
    serial: str
    product: str
    role: str
    ext_address: str
    rloc16: str
    thread_attached: bool
    commissioned: bool


@dataclass(frozen=True)
class ThreadDiagParentRecord:
    kind: str
    schema: int
    serial: str
    product: str
    source: str
    parent_ext_address: str
    parent_rloc16: str
    link_quality_in: int
    link_quality_out: int
    parent_avg_rssi_dbm: float
    parent_last_rssi_dbm: float


@dataclass(frozen=True)
class ThreadDiagNeighborRecord:
    kind: str
    schema: int
    serial: str
    product: str
    source: str
    ext_address: str
    rloc16: str
    role: str
    is_child: bool
    full_thread_device: bool
    link_quality_in: int
    link_quality_out: int
    avg_rssi_dbm: float
    last_rssi_dbm: float
    age_s: int


ThreadDiagRecord = ThreadDiagNodeRecord | ThreadDiagParentRecord | ThreadDiagNeighborRecord


def _parse_fields(payload: str) -> dict[str, str]:
    fields = {match.group(1): match.group(2) for match in _KEY_VALUE_RE.finditer(payload)}
    if not fields:
        raise ValueError("no key=value fields found")
    return fields


def _require_text(fields: dict[str, str], key: str) -> str:
    value = fields.get(key, "").strip()
    if not value:
        raise ValueError(f"missing {key}")
    return value


def _parse_schema(fields: dict[str, str]) -> int:
    value = _require_text(fields, "schema")
    if value != "1":
        raise ValueError(f"unsupported schema {value}")
    return 1


def _normalize_ext_address(value: str, key: str) -> str:
    text = value.strip().lower().replace(":", "").replace("-", "")
    if not _HEX16_RE.fullmatch(text):
        raise ValueError(f"invalid {key}")
    return text


def _normalize_rloc16(value: str, key: str) -> str:
    text = value.strip().lower()
    if text.startswith("0x"):
        candidate = text
    elif re.fullmatch(r"[0-9a-fA-F]{1,4}", value.strip()):
        candidate = "0x" + value.strip().lower()
    else:
        raise ValueError(f"invalid {key}")
    if not _RLOC16_RE.fullmatch(candidate):
        raise ValueError(f"invalid {key}")
    return "0x" + candidate[2:].zfill(4)


def _parse_bool01(value: str, key: str) -> bool:
    text = value.strip().lower()
    if text not in {"0", "1"}:
        raise ValueError(f"invalid {key}")
    return text == "1"


def _parse_int(value: str, key: str) -> int:
    text = value.strip()
    if not _INT_RE.fullmatch(text):
        raise ValueError(f"invalid {key}")
    return int(text)


def _parse_float(value: str, key: str) -> float:
    text = value.strip()
    if not (_INT_RE.fullmatch(text) or _FLOAT_RE.fullmatch(text)):
        raise ValueError(f"invalid {key}")
    return float(text)


def _parse_lqi(value: str, key: str, *, allow_negative_one: bool = False) -> int:
    parsed = _parse_int(value, key)
    if allow_negative_one and parsed == -1:
        return parsed
    if parsed < 0 or parsed > 3:
        raise ValueError(f"invalid {key}")
    return parsed


def _parse_role(value: str, *, allowed: set[str], key: str = "role") -> str:
    text = value.strip().lower()
    if text not in allowed:
        raise ValueError(f"invalid {key}")
    return text


def _parse_node(fields: dict[str, str]) -> ThreadDiagNodeRecord:
    return ThreadDiagNodeRecord(
        kind="node",
        schema=_parse_schema(fields),
        serial=_require_text(fields, "serial"),
        product=_require_text(fields, "product"),
        role=_parse_role(_require_text(fields, "role"), allowed={"child", "router", "leader"}),
        ext_address=_normalize_ext_address(_require_text(fields, "ext_address"), "ext_address"),
        rloc16=_normalize_rloc16(_require_text(fields, "rloc16"), "rloc16"),
        thread_attached=_parse_bool01(_require_text(fields, "thread_attached"), "thread_attached"),
        commissioned=_parse_bool01(_require_text(fields, "commissioned"), "commissioned"),
    )


def _parse_parent(fields: dict[str, str]) -> ThreadDiagParentRecord:
    avg_rssi_raw = fields.get("parent_avg_rssi_dbm", "").strip()
    last_rssi_raw = fields.get("parent_last_rssi_dbm", "").strip()
    fallback_rssi_raw = fields.get("parent_rssi_dbm", "").strip()
    if not avg_rssi_raw and fallback_rssi_raw:
        avg_rssi_raw = fallback_rssi_raw
    if not last_rssi_raw and fallback_rssi_raw:
        last_rssi_raw = fallback_rssi_raw
    return ThreadDiagParentRecord(
        kind="parent",
        schema=_parse_schema(fields),
        serial=_require_text(fields, "serial"),
        product=_require_text(fields, "product"),
        source=_parse_role(_require_text(fields, "source"), allowed={"reported-by-node"}, key="source"),
        parent_ext_address=_normalize_ext_address(_require_text(fields, "parent_ext_address"), "parent_ext_address"),
        parent_rloc16=_normalize_rloc16(_require_text(fields, "parent_rloc16"), "parent_rloc16"),
        link_quality_in=_parse_lqi(_require_text(fields, "link_quality_in"), "link_quality_in"),
        link_quality_out=_parse_lqi(_require_text(fields, "link_quality_out"), "link_quality_out"),
        parent_avg_rssi_dbm=_parse_float(avg_rssi_raw, "parent_avg_rssi_dbm"),
        parent_last_rssi_dbm=_parse_float(last_rssi_raw, "parent_last_rssi_dbm"),
    )


def _parse_neighbor(fields: dict[str, str]) -> ThreadDiagNeighborRecord:
    return ThreadDiagNeighborRecord(
        kind="neighbor",
        schema=_parse_schema(fields),
        serial=_require_text(fields, "serial"),
        product=_require_text(fields, "product"),
        source=_parse_role(_require_text(fields, "source"), allowed={"observed-by-parent"}, key="source"),
        ext_address=_normalize_ext_address(_require_text(fields, "ext_address"), "ext_address"),
        rloc16=_normalize_rloc16(_require_text(fields, "rloc16"), "rloc16"),
        role=_parse_role(_require_text(fields, "role"), allowed={"router", "child", "unknown"}),
        is_child=_parse_bool01(_require_text(fields, "is_child"), "is_child"),
        full_thread_device=_parse_bool01(_require_text(fields, "full_thread_device"), "full_thread_device"),
        link_quality_in=_parse_lqi(_require_text(fields, "link_quality_in"), "link_quality_in"),
        link_quality_out=_parse_lqi(
            _require_text(fields, "link_quality_out"),
            "link_quality_out",
            allow_negative_one=True,
        ),
        avg_rssi_dbm=_parse_float(_require_text(fields, "avg_rssi_dbm"), "avg_rssi_dbm"),
        last_rssi_dbm=_parse_float(_require_text(fields, "last_rssi_dbm"), "last_rssi_dbm"),
        age_s=_parse_int(_require_text(fields, "age_s"), "age_s"),
    )


def parse_thread_diag_line(line: str) -> ThreadDiagRecord | None:
    match = _PREFIX_RE.match(line or "")
    if not match:
        return None
    prefix, payload = match.groups()
    fields = _parse_fields(payload)
    if prefix == "ThreadDiag":
        return _parse_node(fields)
    if prefix == "ThreadDiagParent":
        return _parse_parent(fields)
    if prefix == "ThreadDiagNeighbor":
        return _parse_neighbor(fields)
    return None


def parse_thread_diag_lines(lines: list[str]) -> tuple[list[ThreadDiagRecord], list[dict[str, Any]]]:
    records: list[ThreadDiagRecord] = []
    errors: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        try:
            record = parse_thread_diag_line(line)
        except ValueError as exc:
            errors.append({"index": idx, "line": line, "error": str(exc)})
            continue
        if record is None:
            errors.append({"index": idx, "line": line, "error": "unsupported line"})
            continue
        records.append(record)
    return records, errors


def record_to_dict(record: ThreadDiagRecord) -> dict[str, Any]:
    return asdict(record)
