"""The check registry.

Two kinds of check, one registry. Adding a new check is one decorated
function plus one YAML entry; the executor never changes.

CELL CHECK
    Receives a column Series and returns (evaluated_mask, failed_mask,
    reason). Most rules are of this kind.

STATEFUL CHECK
    A class that accumulates across chunks and finalises once at the end.
    Needed when a verdict depends on the whole dataset rather than one row.
    Uniqueness is the only current user.

    The INTEGRITY dimension will be the second user: orphaned foreign keys
    require seeing both datasets before any verdict is possible. The
    interface below is already sufficient for it -- what integrity
    additionally needs is a multi-dataset RunContext (see dqa/config.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

import pandas as pd

from ..models import Rule, Violation


@dataclass
class CellOutcome:
    """Result of a cell check over one chunk."""
    evaluated: pd.Series  # bool mask: rows the rule actually applied to
    failed: pd.Series     # bool mask: rows that failed
    reason: str           # human-readable, shown in the examples drawer


CellCheck = Callable[[pd.Series, Rule, dict], CellOutcome]


class StatefulCheck(Protocol):
    """Accumulates over chunks, produces a verdict at the end."""

    def accumulate(self, chunk: pd.DataFrame, rule: Rule, ctx: dict) -> None: ...

    def finalise(self, rule: Rule, ctx: dict) -> tuple[int, int, list[Violation]]:
        """Return (evaluated, failed, violations)."""
        ...


CELL_CHECKS: dict[str, CellCheck] = {}
STATEFUL_CHECKS: dict[str, type] = {}


def cell_check(name: str):
    def decorator(fn: CellCheck) -> CellCheck:
        if name in CELL_CHECKS:
            raise ValueError(f"Duplicate cell check registered: {name}")
        CELL_CHECKS[name] = fn
        return fn
    return decorator


def stateful_check(name: str):
    def decorator(cls: type) -> type:
        if name in STATEFUL_CHECKS:
            raise ValueError(f"Duplicate stateful check registered: {name}")
        STATEFUL_CHECKS[name] = cls
        return cls
    return decorator


def is_registered(name: str) -> bool:
    return name in CELL_CHECKS or name in STATEFUL_CHECKS


def kind_of(name: str) -> str:
    if name in CELL_CHECKS:
        return "cell"
    if name in STATEFUL_CHECKS:
        return "stateful"
    raise KeyError(f"Unknown check: {name}")


# --------------------------------------------------------------------------
# Helpers used by the checks themselves
# --------------------------------------------------------------------------
def present_mask(series: pd.Series) -> pd.Series:
    """Rows where a value is actually present.

    Most rules should not penalise a row twice -- a null email is a
    completeness failure, not also a validity failure. Validity, consistency
    and accuracy checks therefore evaluate only populated cells.
    """
    from .. import config

    if series.isna().all():
        return pd.Series([False] * len(series), index=series.index)
    as_str = series.fillna("").astype(str).str.strip()
    return (as_str != "") & (~as_str.str.lower().isin(config.PLACEHOLDER_VALUES))


def empty_outcome(series: pd.Series, reason: str = "") -> CellOutcome:
    false_mask = pd.Series([False] * len(series), index=series.index)
    return CellOutcome(evaluated=false_mask, failed=false_mask, reason=reason)
