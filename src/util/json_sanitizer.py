"""JSON sanitization and repair for LLM responses.

Local Gemma will occasionally produce malformed JSON. Parse strategy
used by agents:

    1. sanitize_json_string(raw) → json.loads
    2. extract_json(raw)         → json.loads
    3. repair_json(raw)          → json.loads
    4. give up, log, skip the turn's update

Adapted from reference/json_sanitizer.py — trimmed to what this project
uses (no comic-era helpers).
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


_NULL_BYTE_PATTERN = re.compile(r"\x00")
_JSON_NULL_ESCAPE_PATTERN = re.compile(r"\\u0000")
_MALFORMED_UNICODE_ESCAPE = re.compile(
    r"\\u(?:[0-9a-fA-F]{0,3}(?=[^0-9a-fA-F]|$))"
)
_INVISIBLE_CHARS = re.compile(
    r"[​‌‍‎‏"
    r"‪-‮"
    r"﻿"
    r"￼"
    r"￾￿"
    r"]"
)


def sanitize_text(text: str) -> str:
    if not text:
        return text
    text = _NULL_BYTE_PATTERN.sub("", text)
    text = _INVISIBLE_CHARS.sub("", text)
    text = "".join(
        ch for ch in text
        if ch in ("\t", "\n", "\r") or unicodedata.category(ch) != "Cc"
    )
    text = unicodedata.normalize("NFC", text)
    return text


def sanitize_json_string(raw: str) -> str:
    if not raw:
        return raw
    raw = _JSON_NULL_ESCAPE_PATTERN.sub("", raw)
    raw = _NULL_BYTE_PATTERN.sub("", raw)
    raw = raw.replace("￼", "")
    raw = _MALFORMED_UNICODE_ESCAPE.sub("", raw)
    return raw


def sanitize_parsed_response(data: Any) -> Any:
    """Deep-clean string values in a parsed JSON structure (idempotent)."""
    if isinstance(data, str):
        return sanitize_text(data)
    if isinstance(data, dict):
        return {k: sanitize_parsed_response(v) for k, v in data.items()}
    if isinstance(data, list):
        return [sanitize_parsed_response(item) for item in data]
    return data


def extract_json(text: str) -> str | None:
    """Pull the first JSON object out of surrounding text."""
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        candidate = text[first_brace:last_brace + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            return None
    return None


def repair_json(text: str) -> str | None:
    """Best-effort repair for truncated/garbled top-level objects.

    Walks depth while respecting JSON string escaping; if the outer
    object never closes cleanly, truncate to the last top-level value
    boundary and close the object ourselves.
    """
    start = text.find("{")
    if start < 0:
        return None

    in_string = False
    escape_next = False
    depth = 0
    last_value_end = -1

    for i in range(start, len(text)):
        ch = text[i]

        if escape_next:
            escape_next = False
            continue
        if in_string:
            if ch == "\\":
                escape_next = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 1:
                last_value_end = i + 1
            elif depth == 0:
                candidate = text[start:i + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    break

    if last_value_end > start:
        truncated = text[start:last_value_end].rstrip().rstrip(",").rstrip()
        truncated += "\n}"
        try:
            json.loads(truncated)
            return truncated
        except json.JSONDecodeError:
            return None

    return None


def parse_structured_response(raw: str) -> dict | None:
    """Run the full repair pipeline and return a parsed dict, or None.

    Order: direct parse → extract → repair → fail. Results are deep-
    sanitized before returning so downstream Pydantic validation sees
    clean strings.
    """
    if not raw:
        return None

    cleaned = sanitize_json_string(raw)

    try:
        data = json.loads(cleaned)
        return sanitize_parsed_response(data)
    except json.JSONDecodeError:
        pass

    extracted = extract_json(cleaned)
    if extracted is not None:
        try:
            return sanitize_parsed_response(json.loads(extracted))
        except json.JSONDecodeError:
            pass

    repaired = repair_json(cleaned)
    if repaired is not None:
        try:
            return sanitize_parsed_response(json.loads(repaired))
        except json.JSONDecodeError:
            pass

    return None


def safe_json_dumps(data: Any, **kwargs: Any) -> str:
    kwargs.setdefault("ensure_ascii", False)
    return json.dumps(data, **kwargs)
