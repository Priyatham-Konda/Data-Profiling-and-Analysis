"""Timeliness checks.

Is the data current, and are its dates coherent?

This dimension is the one most likely to be unassessable: a file with no date
column cannot be scored for timeliness at all. When that happens the run
reports `notAssessed` with a reason rather than scoring zero. Scoring a file
zero for timeliness because it happens to have no dates would be actively
misleading in front of a client, which is why the API contract carries an
explicit `notAssessed` field.

NOTE: timeliness occupies the sixth dimension slot in this phase. The
architect's original set named `integrity` instead. Both belong in the final
product; integrity is deferred because it is meaningless on a single
standalone CSV. See dqa/config.py.
"""
from __future__ import annotations

import pandas as pd

from .. import config
from ..models import Rule
from ..rules.registry import CellOutcome, cell_check, empty_outcome, present_mask
from .validity import _parse_dates


@cell_check("not_stale")
def not_stale(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Records not updated within the staleness window."""
    days = int(rule.params.get("days", config.STALENESS_DAYS))
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)

    values = series.fillna("").astype(str).str.strip()
    parsed = _parse_dates(values)
    evaluated = present_mask(series) & parsed.notna()
    failed = evaluated & (parsed < cutoff)

    years = days / 365.25
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason=f"Record has not been updated in over {years:.1f} years",
    )


@cell_check("not_future_dated")
def not_future_dated(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Future dates in a column recording something that already happened."""
    values = series.fillna("").astype(str).str.strip()
    parsed = _parse_dates(values)
    evaluated = present_mask(series) & parsed.notna()
    failed = evaluated & (parsed > pd.Timestamp.now())
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason="Date is in the future for an event that should already have occurred",
    )


@cell_check("within_expected_range")
def within_expected_range(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Dates far outside the bulk of the column's own distribution.

    Uses the 1st and 99th percentile of the column's own dates, widened by a
    margin. Catches 1900-01-01 and 9999-12-31 sentinels that pass a
    plausibility window but are obviously not real.
    """
    margin_days = int(rule.params.get("margin_days", 3650))

    values = series.fillna("").astype(str).str.strip()
    parsed = _parse_dates(values)
    evaluated = present_mask(series) & parsed.notna()
    valid = parsed[evaluated]
    if len(valid) < 20:
        return empty_outcome(series, "Not enough dates to establish a range")

    lower = valid.quantile(0.01) - pd.Timedelta(days=margin_days)
    upper = valid.quantile(0.99) + pd.Timedelta(days=margin_days)
    failed = evaluated & ((parsed < lower) | (parsed > upper))
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason="Date falls far outside the range of the rest of this column",
    )


def is_assessable(profile) -> tuple[bool, str]:
    """Can timeliness be assessed for this dataset at all?

    Returns (assessable, reason). The reason is shown to the user verbatim
    through the `notAssessed` field, so it is written for a person.
    """
    date_columns = [
        c for c in profile.columns
        if c.semantic_type in ("date", "datetime")
        or c.inferred_type in ("date", "datetime")
        or c.date_parse_rate >= 0.8
    ]
    if not date_columns:
        return False, "No date column was detected in this file."

    populated = [c for c in date_columns if c.fill_rate > 0.05]
    if not populated:
        return False, (
            "The file has date columns but they are effectively empty, "
            "so currency of the data cannot be assessed."
        )

    if not any(c.is_cde for c in populated):
        return False, (
            "Date columns were found but none were selected as critical data "
            "elements, so timeliness was not assessed."
        )

    return True, ""
