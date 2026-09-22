"""Completeness checks.

Are values present where they are required? Deliberately the simplest
dimension: it answers presence only, never correctness. A populated but wrong
value is an accuracy or validity problem, not a completeness one.
"""
from __future__ import annotations

import pandas as pd

from .. import config
from ..models import Rule
from ..rules.registry import CellOutcome, cell_check


def _all_rows(series: pd.Series) -> pd.Series:
    return pd.Series([True] * len(series), index=series.index)


@cell_check("not_null")
def not_null(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Null, empty string or whitespace-only."""
    as_str = series.fillna("").astype(str).str.strip()
    failed = series.isna() | (as_str == "")
    return CellOutcome(
        evaluated=_all_rows(series),
        failed=failed,
        reason="Required field is empty",
    )


@cell_check("not_placeholder")
def not_placeholder(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Values that are technically present but mean 'missing'.

    'N/A', 'UNKNOWN', 'XXX' and friends pass a null check while carrying no
    information. Clients consistently underestimate how much of this they
    have, which makes it a persuasive finding.
    """
    as_str = series.fillna("").astype(str).str.strip()
    evaluated = as_str != ""
    failed = as_str.str.lower().isin(config.PLACEHOLDER_VALUES) & evaluated
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason="Placeholder value carries no information",
    )


@cell_check("fill_rate_above")
def fill_rate_above(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Flag every empty cell when the column's overall fill rate is poor.

    The threshold is evaluated against the profile, not the chunk, so the
    verdict is stable across chunks.
    """
    threshold = float(rule.params.get("threshold", 0.95))
    profile = ctx.get("profile")
    column = profile.by_name(rule.column) if profile and rule.column else None

    if column is None or column.fill_rate >= threshold:
        false_mask = pd.Series([False] * len(series), index=series.index)
        return CellOutcome(evaluated=_all_rows(series), failed=false_mask,
                           reason="Column fill rate is acceptable")

    as_str = series.fillna("").astype(str).str.strip()
    failed = series.isna() | (as_str == "")
    return CellOutcome(
        evaluated=_all_rows(series),
        failed=failed,
        reason=f"Column is only {column.fill_rate:.0%} populated, below the "
               f"{threshold:.0%} threshold",
    )
