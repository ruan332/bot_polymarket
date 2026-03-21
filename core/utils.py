from __future__ import annotations

import hashlib
import json
import math
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


def stable_hash(raw: str, length: int = 12) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def sigmoid(value: float) -> float:
    if value >= 0:
        exp = math.exp(-value)
        return 1 / (1 + exp)
    exp = math.exp(value)
    return exp / (1 + exp)


def logit(probability: float, epsilon: float = 1e-6) -> float:
    bounded = clamp(probability, epsilon, 1 - epsilon)
    return math.log(bounded / (1 - bounded))
