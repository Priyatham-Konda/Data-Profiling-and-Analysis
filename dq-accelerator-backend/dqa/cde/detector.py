"""Critical Data Element detection.

CDE selection drives every score in the report, so the detector is
deterministic and every contribution to the score is recorded. The reasoning
is surfaced through GET /runs/{id}/profile and can be overridden through
PUT /runs/{id}/cdes.
"""
from __future__ import annotations

import re

from .. import config
from ..models import ColumnProfile, DatasetProfile
from ..profiling.semantic import BUSINESS_SEMANTIC_TYPES

_METADATA_RES = [re.compile(p, re.I) for p in config.METADATA_NAME_PATTERNS]
_BUSINESS_RES = [re.compile(p, re.I) for p in config.BUSINESS_NAME_PATTERNS]


def _matches_metadata(name: str) -> bool:
    return any(r.search(name) for r in _METADATA_RES)


def _matches_business(name: str) -> bool:
    return any(r.search(name) for r in _BUSINESS_RES)


def score_column(profile: ColumnProfile, row_count: int) -> tuple[float, list[str]]:
    """Return (score in 0..1, human-readable reasons)."""
    name = (profile.name or "").strip().lower()
    reasons: list[str] = []
    score = 0.0

    # An entirely empty column has nothing to assess, whatever it is called.
    if profile.fill_rate == 0.0:
        return 0.0, ["Column is entirely empty, nothing to assess"]

    if _matches_metadata(name):
        score += config.CDE_WEIGHTS["metadata_name_match"]
        reasons.append("Matches a metadata column pattern")
    elif _matches_business(name):
        score += config.CDE_WEIGHTS["business_name_match"]
        reasons.append("Name matches a business-entity pattern")

    if profile.semantic_type in BUSINESS_SEMANTIC_TYPES:
        score += config.CDE_WEIGHTS["business_semantic_type"]
        reasons.append(f"Recognised as {profile.semantic_type.replace('_', ' ')}")

    if 0.2 <= profile.fill_rate <= 1.0:
        score += config.CDE_WEIGHTS["healthy_fill_rate"]
        reasons.append(f"Populated in {profile.fill_rate:.0%} of rows")

    if profile.distinct_estimate == 1:
        score += config.CDE_WEIGHTS["single_value"]
        reasons.append("Holds a single repeated value")
    elif profile.distinct_ratio >= 0.9 and profile.inferred_type == "string":
        score += config.CDE_WEIGHTS["high_distinctness"]
        reasons.append("Near-unique values, likely an identifier or name")
    elif row_count > 1000 and profile.distinct_ratio < 0.001:
        score += config.CDE_WEIGHTS["very_low_distinctness"]
        reasons.append("Very low variation across the file")

    score = max(0.0, min(1.0, score))
    return score, reasons


def detect(profile: DatasetProfile) -> DatasetProfile:
    """Annotate every column with its CDE decision, in place."""
    for column in profile.columns:
        score, reasons = score_column(column, profile.row_count)
        column.cde_score = score
        column.cde_reasons = reasons
        column.is_cde = score >= config.CDE_THRESHOLD

    # Safety net: if nothing cleared the threshold the run would assess
    # nothing at all, which is worse than a rough guess. Take the highest
    # scoring third of non-empty columns instead and say so.
    if not any(c.is_cde for c in profile.columns):
        candidates = [c for c in profile.columns if c.fill_rate > 0]
        candidates.sort(key=lambda c: c.cde_score, reverse=True)
        take = max(1, len(candidates) // 3)
        for column in candidates[:take]:
            column.is_cde = True
            column.cde_reasons.append(
                "Selected as a fallback: no column met the detection threshold"
            )
    return profile


def apply_override(profile: DatasetProfile, columns: list[str]) -> list[str]:
    """Force the CDE set to exactly `columns`. Returns unknown column names."""
    known = {c.name for c in profile.columns}
    unknown = [c for c in columns if c not in known]
    if unknown:
        return unknown
    wanted = set(columns)
    for column in profile.columns:
        column.is_cde = column.name in wanted
        if column.is_cde and "Manually selected" not in column.cde_reasons:
            column.cde_reasons = ["Manually selected"]
        elif not column.is_cde and column.cde_score >= config.CDE_THRESHOLD:
            column.cde_reasons = ["Manually excluded"]
    return []
