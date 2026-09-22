"""Consistency checks.

Is one field formatted the same way throughout? Consistency says nothing
about whether a value is correct -- only whether it looks like its
neighbours.

The pattern-signature check is the most productive check in the entire
engine and needs no configuration: it learns each column's normal shape from
the column itself and flags the outliers.
"""
from __future__ import annotations

import pandas as pd

from .. import config
from ..models import Rule
from ..profiling.stats import pattern_mask
from ..rules.registry import CellOutcome, cell_check, empty_outcome, present_mask


@cell_check("pattern_consistent")
def pattern_consistent(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Flag values whose format signature is unusual for their column.

    Values are masked (letter runs -> A, digit runs -> 9, punctuation kept),
    the masks are ranked by frequency, and the smallest set of masks covering
    PATTERN_COVERAGE of the column is treated as normal. Anything else fails.
    """
    coverage = float(rule.params.get("coverage", config.PATTERN_COVERAGE))
    profile = ctx.get("profile")
    column = profile.by_name(rule.column) if profile and rule.column else None
    if column is None or not column.pattern_masks:
        return empty_outcome(series, "No pattern baseline available")

    total = sum(column.pattern_masks.values())
    if total == 0:
        return empty_outcome(series, "No pattern baseline available")

    ranked = sorted(column.pattern_masks.items(), key=lambda kv: kv[1], reverse=True)

    # A column where every value is its own shape (free text, descriptions)
    # has no meaningful format to be consistent with.
    if len(ranked) > 50 or ranked[0][1] / total < 0.10:
        return empty_outcome(series, "Column has no dominant format")

    accepted: set[str] = set()
    running = 0
    for mask, count in ranked:
        accepted.add(mask)
        running += count
        if running / total >= coverage:
            break

    evaluated = present_mask(series)
    values = series.fillna("").astype(str).str.strip()
    masks = values.apply(pattern_mask)
    failed = ~masks.isin(accepted)

    dominant = ranked[0][0]
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason=f"Format differs from the column norm (most values look like '{dominant}')",
    )


@cell_check("consistent_casing")
def consistent_casing(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Mixed casing conventions within one column.

    Only applied where a dominant convention exists; a column that is
    genuinely mixed by nature is not a finding.
    """
    evaluated = present_mask(series)
    values = series.fillna("").astype(str).str.strip()
    considered = values[evaluated]
    if considered.empty:
        return empty_outcome(series, "No values to assess")

    def style(v: str) -> str:
        letters = [c for c in v if c.isalpha()]
        if not letters:
            return "none"
        if all(c.isupper() for c in letters):
            return "upper"
        if all(c.islower() for c in letters):
            return "lower"
        if v[:1].isupper() and all(w[:1].isupper() for w in v.split() if w and w[0].isalpha()):
            return "title"
        return "mixed"

    styles = values.apply(style)
    relevant = styles[evaluated & (styles != "none")]
    if relevant.empty:
        return empty_outcome(series, "No alphabetic values")

    counts = relevant.value_counts()
    dominant = counts.index[0]
    share = counts.iloc[0] / counts.sum()
    if share < 0.7:
        return empty_outcome(series, "Column has no dominant casing convention")

    failed = (styles != dominant) & (styles != "none")
    return CellOutcome(
        evaluated=evaluated & (styles != "none"),
        failed=failed,
        reason=f"Casing differs from the column norm ({dominant})",
    )


@cell_check("no_surrounding_whitespace")
def no_surrounding_whitespace(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Leading or trailing whitespace.

    Invisible on screen, but it breaks joins and grouping downstream, which
    makes it a persuasive finding for a technical audience.
    """
    raw = series.fillna("").astype(str)
    evaluated = raw.str.strip() != ""
    failed = raw != raw.str.strip()
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason="Value has leading or trailing whitespace",
    )


@cell_check("single_date_format")
def single_date_format(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """More than one date format inside one column.

    Mixed formats are genuinely dangerous rather than merely untidy: 03/04
    is ambiguous between March 4th and April 3rd, and downstream systems
    will resolve it differently.
    """
    from ..profiling.stats import DATE_FORMATS

    profile = ctx.get("profile")
    column = profile.by_name(rule.column) if profile and rule.column else None
    if column is None or column.inferred_type not in ("date", "datetime"):
        if column is None or column.date_parse_rate < 0.5:
            return empty_outcome(series, "Not a date column")

    evaluated = present_mask(series)
    values = series.fillna("").astype(str).str.strip()

    def fmt_of(v: str) -> str:
        if not v:
            return ""
        for fmt in DATE_FORMATS:
            try:
                pd.to_datetime(v, format=fmt)
                return fmt
            except (ValueError, TypeError):
                continue
        return "other"

    formats = values.apply(fmt_of)
    relevant = formats[evaluated & (formats != "")]
    if relevant.empty:
        return empty_outcome(series, "No parseable dates")

    counts = relevant.value_counts()
    dominant = counts.index[0]
    if len(counts) == 1:
        return empty_outcome(series, "Single consistent date format")

    failed = (formats != dominant) & (formats != "")
    return CellOutcome(
        evaluated=evaluated & (formats != ""),
        failed=failed,
        reason=f"Date format differs from the column norm ({dominant})",
    )
