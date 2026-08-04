import re
from typing import Optional


def sample_rate_text_to_hz(rate_text: str) -> Optional[float]:
    """Convert MSCL/SensorConnect rate labels to Hertz."""
    s = str(rate_text or "").strip().lower().replace("-", " ")
    if not s:
        return None

    m = re.search(r"(\d+)\s*khz", s)
    if m:
        return float(int(m.group(1)) * 1000)

    m = re.search(r"(\d+)\s*hz", s)
    if m:
        return float(int(m.group(1)))

    m = re.search(r"every\s+(\d+)\s*second", s)
    if m:
        sec = int(m.group(1))
        return (1.0 / sec) if sec > 0 else None

    m = re.search(r"every\s+(\d+)\s*minute", s)
    if m:
        sec = int(m.group(1)) * 60
        return (1.0 / sec) if sec > 0 else None

    m = re.search(r"every\s+(\d+)\s*hour", s)
    if m:
        sec = int(m.group(1)) * 3600
        return (1.0 / sec) if sec > 0 else None

    return None


__all__ = ["sample_rate_text_to_hz"]
