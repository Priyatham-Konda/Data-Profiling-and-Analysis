"""Semantic type inference.

Semantic type is what makes rules possible without a schema: knowing a column
holds email addresses is what lets us validate it. Two independent signals
are combined -- the column NAME against a pattern list, and the VALUES
against a validator. Agreement raises confidence; disagreement is itself a
finding worth reporting.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Optional

from .. import config

# --------------------------------------------------------------------------
# Value-level validators
# --------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
URL_RE = re.compile(r"^(https?://|www\.)[^\s]+$", re.I)
PHONE_RE = re.compile(r"^[\+\(]?[\d][\d\s\-\(\)\.]{6,20}$")
POSTCODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9\s\-]{2,9}$", re.I)
US_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
UK_POSTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.I)
IN_PIN_RE = re.compile(r"^\d{6}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_/\.]{2,}$")
PERSON_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z\s\.\'\-]{1,60}$")
AMOUNT_RE = re.compile(r"^-?[\$\u20ac\u00a3\u20b9]?\s?-?[\d,]+(\.\d+)?$")
BOOLEAN_VALUES = {
    "true", "false", "yes", "no", "y", "n", "1", "0", "t", "f",
    "active", "inactive", "enabled", "disabled",
}

# --------------------------------------------------------------------------
# Name-level patterns, most specific first
# --------------------------------------------------------------------------
NAME_PATTERNS: list[tuple[str, str]] = [
    ("email", r"e[_\s-]?mail"),
    ("phone", r"phone|mobile|cell|telephone|fax|contact[_\s-]?(no|number)"),
    ("postcode", r"zip|postal|post[_\s-]?code|pin[_\s-]?code"),
    ("country", r"^country|country$|nation"),
    ("state", r"^state|state$|province|region$"),
    ("city", r"^city|city$|town$|locality"),
    ("address", r"address|street|addr[_\s-]?line"),
    ("currency_code", r"currency([_\s-]?code)?$|^ccy"),
    ("amount", r"amount|price|cost|revenue|salary|balance|total|value$|fee$|charge"),
    ("date", r"date$|^date|birth|dob|expir|due$"),
    ("datetime", r"timestamp|datetime|_at$|_ts$"),
    ("person_name", r"(first|last|middle|given|sur|full|contact)[_\s-]?name|^name$"),
    ("org_name", r"(company|organi[sz]ation|business|account|vendor|supplier|client)[_\s-]?name"),
    ("url", r"url|website|web[_\s-]?site|link$|domain$"),
    ("identifier", r"^id$|[_\s-]id$|^.*_no$|number$|code$|sku|reference|ref$"),
    ("boolean_flag", r"^is[_\s-]|^has[_\s-]|flag$|^active$|^enabled$"),
    ("category", r"type$|category|status$|segment|class$|tier$|group$"),
]

VALUE_VALIDATORS: dict[str, Callable[[str], bool]] = {
    "email": lambda v: bool(EMAIL_RE.match(v)),
    "url": lambda v: bool(URL_RE.match(v)),
    "phone": lambda v: bool(PHONE_RE.match(v)) and sum(c.isdigit() for c in v) >= 7,
    "currency_code": lambda v: bool(CURRENCY_RE.match(v)),
    "postcode": lambda v: bool(
        US_ZIP_RE.match(v) or UK_POSTCODE_RE.match(v) or IN_PIN_RE.match(v) or POSTCODE_RE.match(v)
    ),
    "boolean_flag": lambda v: v.strip().lower() in BOOLEAN_VALUES,
    "amount": lambda v: bool(AMOUNT_RE.match(v)),
    "person_name": lambda v: bool(PERSON_NAME_RE.match(v)) and not v.strip().isdigit(),
    "identifier": lambda v: bool(IDENTIFIER_RE.match(v)),
}

# Semantic types that count as "business meaningful" for CDE scoring.
BUSINESS_SEMANTIC_TYPES = {
    "email", "phone", "person_name", "org_name", "address", "postcode",
    "country", "state", "city", "currency_code", "amount", "date",
    "datetime", "identifier", "url",
}


def semantic_from_name(column_name: str) -> Optional[str]:
    name = (column_name or "").strip().lower()
    for semantic, pattern in NAME_PATTERNS:
        if re.search(pattern, name):
            return semantic
    return None


def value_match_rate(values: Iterable[str], semantic: str) -> float:
    validator = VALUE_VALIDATORS.get(semantic)
    if validator is None:
        return 0.0
    vals = [v for v in values if isinstance(v, str) and v.strip()]
    if not vals:
        return 0.0
    hits = 0
    for v in vals:
        try:
            if validator(v):
                hits += 1
        except Exception:
            continue
    return hits / len(vals)


def semantic_from_values(values: list[str]) -> tuple[Optional[str], float]:
    """Best-matching semantic type derived from values alone."""
    best, best_rate = None, 0.0
    # Ordered so that more specific types win ties.
    for semantic in ("email", "url", "currency_code", "phone", "postcode",
                     "boolean_flag", "amount", "identifier", "person_name"):
        rate = value_match_rate(values, semantic)
        if rate > best_rate:
            best, best_rate = semantic, rate
    if best_rate < config.SEMANTIC_MATCH_RATE:
        return None, best_rate
    return best, best_rate


def detect_semantic_type(
    column_name: str,
    values: list[str],
    inferred_type: str,
) -> tuple[str, float, bool]:
    """Return (semantic_type, confidence, name_value_conflict).

    A conflict means the name says one thing and the values say another --
    for instance a column called `email` where only 40% of values are valid
    emails. That is reported as a finding, not silently resolved.
    """
    name_guess = semantic_from_name(column_name)
    value_guess, value_rate = semantic_from_values(values)

    # Types the values cannot confirm on their own.
    unverifiable = {"date", "datetime", "country", "state", "city",
                    "address", "org_name", "category"}

    if name_guess and value_guess and name_guess == value_guess:
        return name_guess, max(value_rate, 0.9), False

    if name_guess and name_guess in unverifiable:
        if name_guess in ("date", "datetime") and inferred_type not in ("date", "datetime"):
            # Name says date, values are not parseable dates.
            return name_guess, 0.5, True
        return name_guess, 0.75, False

    if name_guess and value_guess and name_guess != value_guess:
        conflict_rate = value_match_rate(values, name_guess)
        if conflict_rate < config.SEMANTIC_MATCH_RATE:
            # Trust the name for rule selection -- the point is to flag the
            # values that do not conform -- but record the conflict.
            return name_guess, conflict_rate, True
        return name_guess, conflict_rate, False

    if name_guess:
        conflict_rate = value_match_rate(values, name_guess)
        has_validator = name_guess in VALUE_VALIDATORS
        conflict = has_validator and conflict_rate < config.SEMANTIC_MATCH_RATE
        return name_guess, conflict_rate if has_validator else 0.7, conflict

    if value_guess:
        return value_guess, value_rate, False

    if inferred_type in ("integer", "decimal"):
        return "amount", 0.3, False
    if inferred_type in ("date", "datetime"):
        return inferred_type, 0.8, False
    if inferred_type == "boolean":
        return "boolean_flag", 0.8, False
    return "free_text", 0.0, False
