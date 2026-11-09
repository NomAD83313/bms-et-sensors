from typing import Optional

try:
    from mscl_utils import sample_rate_text_to_hz
except ImportError:  # pragma: no cover - test/module import path fallback
    from app.mscl.mscl_utils import sample_rate_text_to_hz


def is_tc_link_200_model(model) -> bool:
    s = str(model or "").strip().lower()
    return ("tc-link-200" in s) or s.startswith("63104100")


def rate_label_to_hz(label) -> Optional[float]:
    return sample_rate_text_to_hz(str(label or ""))


def rate_label_to_interval_seconds(label) -> Optional[float]:
    s = str(label or "").strip().lower()
    if not s.startswith("every "):
        return None
    parts = s.split()
    if len(parts) < 3:
        return None
    try:
        v = float(parts[1])
    except Exception:
        return None
    unit = parts[2]
    if unit.startswith("second"):
        return v
    if unit.startswith("minute"):
        return v * 60.0
    if unit.startswith("hour"):
        return v * 3600.0
    return None


def sample_rate_label(rate_enum, rate_obj=None, rate_map=None) -> str:
    try:
        rid = int(rate_enum)
    except Exception:
        rid = None

    txt = str(rate_obj if rate_obj is not None else "").strip()
    if txt and txt.lower() not in ("none", "null"):
        txt_l = txt.lower()
        numeric_echo = False
        if rid is not None:
            try:
                numeric_echo = int(float(txt_l)) == rid and txt_l.replace(".", "", 1).isdigit()
            except Exception:
                numeric_echo = False
        if not numeric_echo:
            return txt

    lookup = dict(rate_map or {})
    if rid is not None and rid in lookup:
        return lookup[rid]
    if rid is not None:
        return f"Value {rid}"
    return "N/A"


def filter_sample_rates_for_model(model, supported_rates, current_rate, rate_map, tc_link_200_rate_enums):
    rates = []
    seen = set()
    lookup = dict(rate_map or {})
    tc_rates = set(tc_link_200_rate_enums or set())
    for r in list(supported_rates or []):
        try:
            rid = int(r.get("enum_val"))
        except Exception:
            continue
        if rid in seen:
            continue
        seen.add(rid)
        rates.append({"enum_val": rid, "str_val": str(r.get("str_val") or lookup.get(rid, f"Value {rid}"))})

    def _allowed_tc200_oem(rate_item):
        allowed_interval_sec = {2, 5, 10, 30, 60, 120, 300, 600, 1800, 3600}
        try:
            rid = int(rate_item.get("enum_val"))
        except Exception:
            rid = None
        lbl = str(rate_item.get("str_val") or "").strip()
        hz = rate_label_to_hz(lbl)
        interval_sec = rate_label_to_interval_seconds(lbl)
        if rid is not None and rid in tc_rates:
            return True
        if hz is not None and hz <= 128.0:
            return True
        if interval_sec is not None and int(interval_sec) in allowed_interval_sec:
            return True
        return False

    if is_tc_link_200_model(model):
        rates = [x for x in rates if _allowed_tc200_oem(x)]
        rates.sort(
            key=lambda x: (
                0 if rate_label_to_hz(x.get("str_val")) is not None else (1 if rate_label_to_interval_seconds(x.get("str_val")) is not None else 2),
                -(rate_label_to_hz(x.get("str_val")) or 0.0),
                rate_label_to_interval_seconds(x.get("str_val")) or 0.0,
                str(x.get("str_val") or ""),
            )
        )

    if current_rate is not None:
        try:
            cur = int(current_rate)
            cur_item = {"enum_val": cur, "str_val": lookup.get(cur, f"Value {cur}")}
            if is_tc_link_200_model(model) and not _allowed_tc200_oem(cur_item):
                return rates
            if all(int(x.get("enum_val")) != cur for x in rates if x.get("enum_val") is not None):
                rates.insert(0, cur_item)
        except Exception:
            pass
    return rates


__all__ = [
    "filter_sample_rates_for_model",
    "is_tc_link_200_model",
    "rate_label_to_hz",
    "rate_label_to_interval_seconds",
    "sample_rate_label",
]
