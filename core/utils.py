from __future__ import annotations

import json
import re
from typing import Any


def sanitize_text(value: str, max_length: int = 500) -> str:
    clean = re.sub(r"[\x00-\x1f\x7f]", " ", value or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:max_length]


def parse_json_object(raw: str) -> dict[str, Any]:
    candidate = (raw or "").strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if "\n" in candidate:
            candidate = candidate.split("\n", 1)[1]
    if candidate.endswith("```"):
        candidate = candidate[:-3].strip()
    return json.loads(candidate)


def extract_first_float(raw: str, patterns: list[str], default: float | None = None) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return default
