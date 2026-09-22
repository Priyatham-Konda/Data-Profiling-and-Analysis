"""Rule execution.

The important property of this module, and the thing the API contract calls
out explicitly: counters and example violations are produced in the SAME pass
over the data. `total` in the examples endpoint and `passRate` in the
dimension drawer come from one execution, so they cannot disagree.
"""
from __future__ import annotations

from typing import Callable, Optional

import pandas as pd

from .. import config
from ..models import Rule, RuleResult, Violation
from .registry import CELL_CHECKS, STATEFUL_CHECKS, kind_of

ROW_COL = "__row__"


class RuleExecutor:
    """Executes a set of expanded rules over a stream of chunks."""

    def __init__(self, rules: list[Rule], ctx: dict, example_cap: int | None = None):
        self.rules = rules
        self.ctx = ctx
        self.example_cap = example_cap or config.EXAMPLE_CAP

        self.results: dict[str, RuleResult] = {
            rule.id: RuleResult(
                rule_id=rule.id,
                rule_name=rule.name,
                dimension=rule.dimension,
                severity=rule.severity,
                column=rule.column,
            )
            for rule in rules
        }
        self.violations: dict[str, list[Violation]] = {rule.id: [] for rule in rules}
        self.errors: dict[str, str] = {}

        # Instantiate stateful checks once for the whole run.
        self._stateful: dict[str, object] = {}
        for rule in rules:
            if kind_of(rule.check) == "stateful":
                self._stateful[rule.id] = STATEFUL_CHECKS[rule.check]()

    # ----------------------------------------------------------------------
    def process_chunk(self, chunk: pd.DataFrame) -> None:
        rows = chunk[ROW_COL] if ROW_COL in chunk.columns else pd.Series(chunk.index)

        for rule in self.rules:
            if rule.id in self.errors:
                continue
            try:
                if kind_of(rule.check) == "stateful":
                    self._stateful[rule.id].accumulate(chunk, rule, self.ctx)
                else:
                    self._process_cell_rule(rule, chunk, rows)
            except Exception as exc:  # one bad rule must not kill the run
                self.errors[rule.id] = f"{type(exc).__name__}: {exc}"

    def _process_cell_rule(self, rule: Rule, chunk: pd.DataFrame, rows: pd.Series) -> None:
        if rule.column is None or rule.column not in chunk.columns:
            return

        series = chunk[rule.column]
        outcome = CELL_CHECKS[rule.check](series, rule, self.ctx)

        evaluated = outcome.evaluated.fillna(False).astype(bool)
        failed = (outcome.failed.fillna(False).astype(bool)) & evaluated

        result = self.results[rule.id]
        result.evaluated += int(evaluated.sum())
        result.failed += int(failed.sum())

        # Capture the first N violations as we go -- never a second pass.
        bucket = self.violations[rule.id]
        remaining = self.example_cap - len(bucket)
        if remaining > 0 and failed.any():
            failing_idx = failed[failed].index[:remaining]
            for idx in failing_idx:
                raw = series.loc[idx]
                bucket.append(
                    Violation(
                        row=int(rows.loc[idx]) if idx in rows.index else int(idx),
                        column=rule.column,
                        value=_display(raw),
                        reason=rule.message or outcome.reason,
                    )
                )

    # ----------------------------------------------------------------------
    def finalise(self) -> tuple[dict[str, RuleResult], dict[str, list[Violation]]]:
        for rule in self.rules:
            if rule.id in self.errors or kind_of(rule.check) != "stateful":
                continue
            try:
                evaluated, failed, violations = self._stateful[rule.id].finalise(
                    rule, self.ctx
                )
                result = self.results[rule.id]
                result.evaluated = evaluated
                result.failed = failed
                self.violations[rule.id] = violations[: self.example_cap]
            except Exception as exc:
                self.errors[rule.id] = f"{type(exc).__name__}: {exc}"

        # Rules that errored are excluded from scoring entirely rather than
        # counted as passing, which would flatter the result.
        for rule_id in self.errors:
            self.results.pop(rule_id, None)
            self.violations.pop(rule_id, None)

        # Rules that never evaluated a single cell are dropped too: a rule
        # with a denominator of zero contributes nothing and renders as a
        # confusing 100% row in the drawer.
        empty = [rid for rid, r in self.results.items() if r.evaluated == 0]
        for rid in empty:
            self.results.pop(rid)
            self.violations.pop(rid, None)

        return self.results, self.violations


def _display(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "(null)"
    text = str(value)
    if not text.strip():
        return "(blank)"
    return text[:200]
