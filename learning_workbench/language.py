from __future__ import annotations

from typing import Any

from opencc import OpenCC


_T2S = OpenCC("t2s")


def to_simplified_chinese(text: str) -> str:
    """Convert display text to Simplified Chinese without changing source files."""
    return _T2S.convert(text)


def simplify_payload(value: Any) -> Any:
    """Recursively convert strings in an API payload to Simplified Chinese."""
    if isinstance(value, str):
        return to_simplified_chinese(value)
    if isinstance(value, list):
        return [simplify_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: simplify_payload(item) for key, item in value.items()}
    return value
