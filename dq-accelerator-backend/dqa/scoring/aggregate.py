"""Score aggregation.

Scores are computed once, here, and read from storage by both the run detail
and the dimension drawer. The API contract notes that a mismatch between the
tile and the drawer is very visible on screen; computing once is how that is
prevented by construction rather than by care.
"""
from __future__ import annotations

from typing import Optional

from .. import config
from ..models import RuleResult


def dimension_score(results: list[RuleResult]) -> Optional[float]:
    """Severity-weighted pass rate across one dimension, 0-100.

    score = 100 * (1 - sum(w * failed) / sum(w * evaluated))

    Returns None when nothing was evaluated, which the caller turns into a
    `notAssessed` entry rather than a zero.
    """
    numerator = 0.0
    denominator = 0.0
    for result in results:
        weight = config.SEVERITY_WEIGHTS.get(result.severity, 1)
        numerator += weight * result.failed
        denominator += weight * result.evaluated

    if denominator == 0:
        return None
    return round(100.0 * (1.0 - numerator / denominator), 1)


def aggregate(
    results: dict[str, RuleResult],
    not_assessed: dict[str, str] | None = None,
) -> tuple[dict[str, Optional[float]], Optional[float], dict[str, str]]:
    """Return (scores by dimension, overall, notAssessed reasons)."""
    not_assessed = dict(not_assessed or {})
    by_dimension: dict[str, list[RuleResult]] = {d: [] for d in config.DIMENSIONS}

    for result in results.values():
        if result.dimension in by_dimension:
            by_dimension[result.dimension].append(result)

    scores: dict[str, Optional[float]] = {}
    for dimension in config.DIMENSIONS:
        if dimension in not_assessed:
            scores[dimension] = None
            continue
        score = dimension_score(by_dimension[dimension])
        if score is None:
            scores[dimension] = None
            not_assessed.setdefault(
                dimension,
                "No applicable rules could be evaluated against this file.",
            )
        else:
            scores[dimension] = score

    assessed = [s for s in scores.values() if s is not None]
    overall = round(sum(assessed) / len(assessed), 1) if assessed else None

    return scores, overall, not_assessed


def rules_for_dimension(
    results: dict[str, RuleResult], dimension: str
) -> list[RuleResult]:
    """Rules of one dimension, worst pass rate first.

    Worst first because the drawer is opened to find out what is wrong, not
    to admire what is right.
    """
    rules = [r for r in results.values() if r.dimension == dimension]
    rules.sort(key=lambda r: (r.pass_rate, r.rule_id))
    return rules


def band(score: Optional[float]) -> str:
    if score is None:
        return "not assessed"
    if score >= 90:
        return "healthy"
    if score >= 70:
        return "needs attention"
    return "critical"
