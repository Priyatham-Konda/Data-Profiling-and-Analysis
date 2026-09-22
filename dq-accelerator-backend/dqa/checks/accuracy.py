"""Accuracy checks.

Are values plausible and non-contradictory? Accuracy is the hardest dimension
to assess without an external source of truth -- knowing that a product
weight is wrong requires knowing the real weight. What can be done without
that, and is done here:

  1. Conformance to reference lists (ISO countries, currencies, US states)
  2. Cross-field contradiction (a postcode that cannot belong to its country)
  3. Impossible values (a future date of birth)
  4. Statistical outliers

This limitation is stated in the report rather than glossed over.

MIGRATION NOTE: the cross-field checks in this module are tagged
`move_to: integrity` in the rule pack. Cross-field dependency is properly an
integrity concern, but integrity is deferred to the next phase, so they live
here rather than being lost. Move them when integrity is implemented.
"""
from __future__ import annotations

import re

import pandas as pd

from .. import config
from ..models import Rule
from ..rules.registry import CellOutcome, cell_check, empty_outcome, present_mask
from .reference import (
    COUNTRY_ALIASES,
    CURRENCY_CODES,
    US_STATES,
    US_ZIP_BY_STATE_PREFIX,
    COMMON_EMAIL_DOMAINS,
    postcode_matches_country,
)
from .validity import _parse_dates, _to_numeric


@cell_check("in_reference_list")
def in_reference_list(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Values absent from a known reference list."""
    which = rule.params.get("list", "")
    lookup = {
        "country": set(COUNTRY_ALIASES),
        "currency": CURRENCY_CODES,
        "us_state": set(US_STATES) | {v.lower() for v in US_STATES.values()},
    }.get(which)

    if lookup is None:
        return empty_outcome(series, f"Unknown reference list '{which}'")

    evaluated = present_mask(series)
    values = series.fillna("").astype(str).str.strip().str.lower()
    failed = ~values.isin(lookup)
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason=f"Value is not a recognised {which.replace('_', ' ')}",
    )


@cell_check("postcode_matches_country")
def postcode_country(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """A postcode whose format cannot belong to its row's country.

    MIGRATION: move to the integrity dimension when it is implemented.
    """
    country_column = _find_column(ctx, ["country"])
    if country_column is None:
        return empty_outcome(series, "No country column to compare against")

    chunk = ctx.get("_chunk")
    if chunk is None or country_column not in chunk.columns:
        return empty_outcome(series, "No country column in this chunk")

    evaluated = present_mask(series) & present_mask(chunk[country_column])
    postcodes = series.fillna("").astype(str).str.strip()
    countries = chunk[country_column].fillna("").astype(str).str.strip()

    failed = pd.Series(
        [
            not postcode_matches_country(p, c) if e else False
            for p, c, e in zip(postcodes, countries, evaluated)
        ],
        index=series.index,
    )
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason="Postcode format does not match the country in this row",
    )


@cell_check("zip_matches_state")
def zip_matches_state(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """US ZIP prefix contradicting the state in the same row.

    MIGRATION: move to the integrity dimension when it is implemented.
    """
    state_column = _find_column(ctx, ["state"])
    chunk = ctx.get("_chunk")
    if state_column is None or chunk is None or state_column not in chunk.columns:
        return empty_outcome(series, "No state column to compare against")

    zips = series.fillna("").astype(str).str.strip()
    states = chunk[state_column].fillna("").astype(str).str.strip().str.upper()
    evaluated = zips.str.match(r"^\d{5}") & (states != "")

    def contradicts(zip_code: str, state: str) -> bool:
        if not zip_code[:3].isdigit():
            return False
        expected = US_ZIP_BY_STATE_PREFIX.get(zip_code[:3])
        if expected is None:
            return False
        normalised = state if len(state) == 2 else _state_abbr(state)
        if normalised is None:
            return False
        return expected != normalised

    failed = pd.Series(
        [contradicts(z, s) if e else False for z, s, e in zip(zips, states, evaluated)],
        index=series.index,
    )
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason="ZIP code does not belong to the state in this row",
    )


@cell_check("email_domain_plausible")
def email_domain_plausible(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Near-miss email domains: gmial.com, hotmial.com, yaho.com.

    A typo'd domain passes format validation perfectly while being
    undeliverable, which makes this a good demonstration of why format
    checking alone is not enough.
    """
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return empty_outcome(series, "rapidfuzz not installed")

    evaluated = present_mask(series) & series.fillna("").astype(str).str.contains("@")
    domains = series.fillna("").astype(str).str.strip().str.lower().str.split("@").str[-1]

    threshold = int(rule.params.get("threshold", 85))

    def suspicious(domain: str) -> bool:
        """A near-miss of a common domain, not merely a similar one.

        The bar is deliberately high. A corporate domain that happens to
        resemble a consumer one is not a typo, and a false positive here is
        expensive: it accuses a client of errors they do not have.
        """
        if not domain or domain in COMMON_EMAIL_DOMAINS:
            return False
        for known in COMMON_EMAIL_DOMAINS:
            if abs(len(domain) - len(known)) > 2:
                continue
            score = fuzz.ratio(domain, known)
            if threshold <= score < 100:
                return True
        return False

    failed = pd.Series(
        [suspicious(d) if e else False for d, e in zip(domains, evaluated)],
        index=series.index,
    )
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason="Email domain closely resembles a common domain and is probably a typo",
    )


@cell_check("not_future_date")
def not_future_date(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    values = series.fillna("").astype(str).str.strip()
    parsed = _parse_dates(values)
    evaluated = present_mask(series) & parsed.notna()
    failed = evaluated & (parsed > pd.Timestamp.now())
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason=rule.message or "Date is in the future, which is not possible for this field",
    )


@cell_check("statistical_outlier")
def statistical_outlier(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Outliers via median absolute deviation.

    MAD rather than standard deviation: business data is routinely skewed and
    contains the very outliers we are hunting, which drag the mean and
    inflate sigma so that the outliers hide inside their own effect. The
    median and MAD are unaffected by them.

    modified z = 0.6745 * (x - median) / MAD
    """
    threshold = float(rule.params.get("threshold", config.MAD_Z_THRESHOLD))
    profile = ctx.get("profile")
    column = profile.by_name(rule.column) if profile and rule.column else None
    if column is None or not column.numeric_stats:
        return empty_outcome(series, "Column is not numeric")

    median = column.numeric_stats.get("median")
    mad = column.numeric_stats.get("mad", 0.0)
    if median is None or not mad:
        # MAD of zero means over half the values are identical; no spread to
        # measure and every differing value would be flagged.
        return empty_outcome(series, "Column has no measurable spread")

    numeric = _to_numeric(series)
    evaluated = present_mask(series) & numeric.notna()
    modified_z = 0.6745 * (numeric - median).abs() / mad
    failed = evaluated & (modified_z > threshold)
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason=f"Value is a statistical outlier (median is {median:g})",
    )


@cell_check("non_negative")
def non_negative(series: pd.Series, rule: Rule, ctx: dict) -> CellOutcome:
    """Negative values in a column that is otherwise never negative."""
    profile = ctx.get("profile")
    column = profile.by_name(rule.column) if profile and rule.column else None
    if column is None or not column.numeric_stats:
        return empty_outcome(series, "Column is not numeric")

    negatives = column.numeric_stats.get("negatives", 0)
    if negatives == 0:
        return empty_outcome(series, "No negative values in this column")
    if negatives > column.populated * 0.05:
        return empty_outcome(series, "Negative values are normal for this column")

    numeric = _to_numeric(series)
    evaluated = present_mask(series) & numeric.notna()
    failed = evaluated & (numeric < 0)
    return CellOutcome(
        evaluated=evaluated,
        failed=failed,
        reason="Negative value in a column that is otherwise never negative",
    )


# --------------------------------------------------------------------------
def _find_column(ctx: dict, semantic_types: list[str]) -> str | None:
    profile = ctx.get("profile")
    if profile is None:
        return None
    for column in profile.columns:
        if column.semantic_type in semantic_types:
            return column.name
    return None


def _state_abbr(name: str) -> str | None:
    lowered = name.strip().lower()
    for abbr, full in US_STATES.items():
        if full.lower() == lowered:
            return abbr
    return None
