"""Validity checks.

Does a value conform to the format or domain its column implies? Validity
evaluates populated cells only -- an empty cell is a completeness failure and
should not be counted twice.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import pandas as pd

from .. import config
from ..models import Rule
from ..profiling.stats import DATE_FORMATS, _clean_numeric
from ..rules.registry import CellOutcome, cell_check, empty_outcome, present_mask


@cell_check("regex_match")
def regex_match(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    pattern = rule.params.get("pattern")
    if not pattern:
        return empty_outcome(series, "No pattern configured")
    flags = re.I if rule.params.get("ignore_case", True) else 0
    compiled = re.compile(pattern, flags)

    evaluated = present_mask(series)
    values = series.fillna("").astype(str).str.strip()
    matches = values.apply(lambda v: bool(compiled.match(v)))
    return CellOutcome(
        evaluated=evaluated,
        failed=~matches,
        reason=rule.message or "Value does not match the expected format",
    )


@cell_check("valid_phone")
def valid_phone(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Real phone validation rather than a regex.

    A regex accepts '(000) 000-0000' and rejects legitimate international
    formats. libphonenumber knows the actual numbering plans.
    """
    region = rule.params.get("region", "US")
    # `strict` uses is_valid_number, which enforces real numbering-plan
    # rules including allocated area codes. That rejects a large share of
    # plausible numbers in practice, so the default is is_possible_number:
    # correct length and shape for the region. Turn strict on only when the
    # client confirms their numbers should be dialable.
    strict = bool(rule.params.get("strict", False))
    evaluated = present_mask(series)
    values = series.fillna("").astype(str).str.strip()

    try:
        import phonenumbers

        def ok(v: str) -> bool:
            if not v:
                return True
            try:
                parsed = phonenumbers.parse(v, region)
                if strict:
                    return phonenumbers.is_valid_number(parsed)
                return phonenumbers.is_possible_number(parsed)
            except Exception:
                return False

        failed = ~values.apply(ok)
    except ImportError:
        digits = values.str.replace(r"\D", "", regex=True)
        failed = (digits.str.len() < 7) | (digits.str.len() > 15)

    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason=f"Not a valid phone number for region {region}",
    )


@cell_check("parseable_date")
def parseable_date(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    evaluated = present_mask(series)
    values = series.fillna("").astype(str).str.strip()
    parsed = _parse_dates(values)
    return CellOutcome(
        evaluated=evaluated,
        failed=parsed.isna(),
        reason="Value is not a recognisable date",
    )


@cell_check("date_in_plausible_range")
def date_in_plausible_range(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Dates outside a sane window are almost always data entry or ETL bugs."""
    min_year = int(rule.params.get("min_year", config.DATE_MIN_YEAR))
    years_ahead = int(rule.params.get("max_years_ahead", config.DATE_MAX_YEARS_AHEAD))
    lower = pd.Timestamp(year=min_year, month=1, day=1)
    upper = pd.Timestamp.now() + pd.Timedelta(days=365 * years_ahead)

    values = series.fillna("").astype(str).str.strip()
    parsed = _parse_dates(values)
    evaluated = present_mask(series) & parsed.notna()
    failed = evaluated & ((parsed < lower) | (parsed > upper))
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason=f"Date falls outside {min_year} to {upper.year}",
    )


@cell_check("in_enum_domain")
def in_enum_domain(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Values outside the column's own inferred domain.

    The domain is derived from the data: where a column has few distinct
    values covering almost every row, the stragglers are typically typos or
    legacy codes. No configuration required.
    """
    profile = ctx.get("profile")
    column = profile.by_name(rule.column) if profile and rule.column else None
    if column is None or not column.is_enum or not column.enum_domain:
        return empty_outcome(series, "Column is not a closed domain")

    domain = {str(v).strip().lower() for v in column.enum_domain}
    evaluated = present_mask(series)
    values = series.fillna("").astype(str).str.strip().str.lower()
    failed = ~values.isin(domain)
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason=f"Value is outside the {len(domain)} values used elsewhere in this column",
    )


@cell_check("numeric_range")
def numeric_range(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    minimum = rule.params.get("min")
    maximum = rule.params.get("max")
    allow_negative = rule.params.get("allow_negative", True)

    evaluated = present_mask(series)
    numeric = _to_numeric(series)
    evaluated = evaluated & numeric.notna()

    failed = pd.Series([False] * len(series), index=series.index)
    reasons = []
    if minimum is not None:
        failed |= numeric < float(minimum)
        reasons.append(f"below {minimum}")
    if maximum is not None:
        failed |= numeric > float(maximum)
        reasons.append(f"above {maximum}")
    if not allow_negative:
        failed |= numeric < 0
        reasons.append("negative")

    return CellOutcome(
        evaluated=evaluated,
        failed=failed & evaluated,
        reason=rule.message or f"Value is {' or '.join(reasons) if reasons else 'out of range'}",
    )


@cell_check("length_within")
def length_within(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    max_len = rule.params.get("max_length")
    min_len = rule.params.get("min_length", 0)

    if max_len is None:
        # Derive from the column's own distribution: values far longer than
        # the norm are usually concatenated or mis-delimited.
        profile = ctx.get("profile")
        column = profile.by_name(rule.column) if profile and rule.column else None
        if column is None or column.mean_length == 0:
            return empty_outcome(series, "No length baseline available")
        max_len = max(int(column.mean_length * 4), column.min_length + 20)

    evaluated = present_mask(series)
    lengths = series.fillna("").astype(str).str.strip().str.len()
    failed = (lengths > int(max_len)) | (lengths < int(min_len))
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason=f"Value length is outside {min_len}-{max_len} characters",
    )


@cell_check("type_conforms")
def type_conforms(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Values that do not match the column's own inferred type.

    If 98% of a column parses as an integer, the other 2% are the finding.
    """
    profile = ctx.get("profile")
    column = profile.by_name(rule.column) if profile and rule.column else None
    if column is None:
        return empty_outcome(series, "No profile available")

    evaluated = present_mask(series)
    values = series.fillna("").astype(str).str.strip()

    if column.inferred_type in ("integer", "decimal"):
        failed = _to_numeric(series).isna() & evaluated
        reason = f"Value is not numeric, but this column is {column.inferred_type}"
    elif column.inferred_type in ("date", "datetime"):
        failed = _parse_dates(values).isna() & evaluated
        reason = "Value is not a date, but this column holds dates"
    else:
        return empty_outcome(series, "No type constraint for string columns")

    return CellOutcome(evaluated=evaluated, failed=failed, reason=reason)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _to_numeric(series: pd.Series) -> pd.Series:
    cleaned = (
        series.fillna("")
        .astype(str)
        .str.strip()
        .str.replace(r"[,\s\$\u20ac\u00a3\u20b9]", "", regex=True)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def _naive_utc(parsed: pd.Series) -> pd.Series:
    """Drop the timezone, having normalised to UTC first.

    Parsed with utc=True, a naive value is localised to UTC and then
    un-localised back to exactly itself, so files without offsets are
    unaffected.
    """
    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        return parsed.dt.tz_localize(None)
    return parsed


def _parse_dates(values: pd.Series) -> pd.Series:
    """Try known formats first, then fall back. Returns NaT where unparseable.

    Everything is normalised to tz-naive UTC. Salesforce exports carry an
    offset (`2026-09-08 17:14:44+00:00`) while hand-maintained files usually
    do not, and one column can hold both. Left mixed, every downstream
    comparison against a naive timestamp raises "Cannot compare tz-naive and
    tz-aware timestamps" -- and because the executor drops any rule that
    errors, the rule vanishes from scoring instead of failing loudly.
    """
    result = pd.Series([pd.NaT] * len(values), index=values.index, dtype="datetime64[ns]")
    remaining = values.str.strip() != ""

    for fmt in DATE_FORMATS:
        if not remaining.any():
            break
        attempt = _naive_utc(
            pd.to_datetime(values[remaining], format=fmt, errors="coerce", utc=True)
        )
        good = attempt.notna()
        if good.any():
            result.loc[attempt[good].index] = attempt[good]
            remaining.loc[attempt[good].index] = False

    if remaining.any():
        attempt = _naive_utc(
            pd.to_datetime(values[remaining], errors="coerce", format="mixed", utc=True)
        )
        good = attempt.notna()
        if good.any():
            result.loc[attempt[good].index] = attempt[good]

    return result
