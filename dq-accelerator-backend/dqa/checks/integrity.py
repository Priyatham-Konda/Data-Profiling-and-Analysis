"""Integrity checks -- relationships that must hold within one dataset.

Integrity is the seventh dimension. It asks whether the relationships a
dataset asserts about itself actually hold: does a code always resolve to
the same description, is a dependent field filled wherever its parent field
is, does a postcode belong to the country recorded on its own row.

SCOPE -- WITHIN ONE FILE
    Everything here works on the single CSV a phase-1 run uploads. Integrity
    *between* datasets -- orphaned foreign keys, mandatory parents with no
    child, lookup codes resolved against a separate table -- still needs a
    second dataset to exist, and is kept commented at the foot of
    rules/default_pack.yaml. That work is purely additive: it needs a
    multi-dataset RunContext and new checks in this module, and changes
    nothing below.

WHY THESE CHECKS DO NOT INVENT FINDINGS
    A standalone CSV declares no foreign keys, so a relationship can only be
    inferred. Both checks here therefore require the file to demonstrate a
    relationship before enforcing it: a dependency counts as real only once
    it already holds across nearly every row, and only the minority of rows
    contradicting it are reported. On a file with no such structure nothing
    is evaluated and the dimension reports notAssessed -- the same honest
    outcome timeliness gives a file with no dates, rather than a vacuous
    score.
"""
from __future__ import annotations

import pandas as pd

from .. import config
from ..models import Rule, Violation
from ..rules.registry import present_mask, stateful_check

ROW_COL = "__row__"


# --------------------------------------------------------------------------
# Candidate selection
# --------------------------------------------------------------------------
def _candidate_columns(ctx: dict) -> list:
    """CDE columns worth relating to one another.

    Restricted to critical data elements because a relationship between two
    fields nobody cares about is not a finding, and because bounding the
    column count bounds the pair count below.
    """
    profile = ctx.get("profile")
    if profile is None:
        return []
    return [c for c in profile.cde_columns if c.fill_rate > 0.0]


def _determinant_candidates(columns: list) -> list:
    """Columns whose values repeat enough to determine something.

    A column with a distinct value on every row determines everything
    trivially and can never be contradicted, so it is excluded: it would
    cost memory and produce no finding. A single-valued column is excluded
    for the mirror reason.
    """
    out = []
    for column in columns:
        distinct = column.distinct_estimate
        if distinct < 2 or distinct > config.INTEGRITY_MAX_KEYS:
            continue
        if column.distinct_capped:
            continue
        if column.distinct_ratio > config.INTEGRITY_MAX_KEY_RATIO:
            continue
        out.append(column)
    return out


def _values(chunk: pd.DataFrame, column: str) -> pd.Series:
    return chunk[column].fillna("").astype(str).str.strip()


def _rows_of(chunk: pd.DataFrame) -> pd.Series:
    return chunk[ROW_COL] if ROW_COL in chunk.columns else pd.Series(chunk.index)


# --------------------------------------------------------------------------
@stateful_check("cardinality_holds")
class CardinalityHolds:
    """A value that determines another value everywhere except here.

    Finds column pairs where one column almost always maps to a single value
    of another -- a product code and its description, a postcode and its
    city, a customer id and its name -- then reports the rows that break the
    mapping. This is the within-dataset form of referential integrity: the
    file contradicts itself about a relationship it otherwise keeps.

    The dependency is never assumed. A pair qualifies only when it holds
    across at least INTEGRITY_CONSISTENCY of its groups over at least
    INTEGRITY_MIN_GROUPS of them, so two genuinely unrelated columns fail to
    qualify and contribute nothing rather than flooding the dimension.
    """

    def __init__(self):
        self.pairs: list[tuple[str, str]] | None = None
        self.first: dict[tuple[str, str], dict[str, tuple[str, int]]] = {}
        self.conflict_rows: dict[tuple[str, str], list[tuple]] = {}
        self.conflict_keys: dict[tuple[str, str], set[str]] = {}
        self.conflict_count: dict[tuple[str, str], int] = {}
        self.evaluated: dict[tuple[str, str], int] = {}

    def _select(self, ctx: dict) -> None:
        columns = _candidate_columns(ctx)
        determinants = _determinant_candidates(columns)
        by_name = {c.name: c for c in columns if not c.distinct_capped}

        pairs: list[tuple[str, str]] = []
        for det in determinants:
            for dep_name, dep in by_name.items():
                if dep_name == det.name:
                    continue
                # A dependent holding more distinct values than its
                # determinant cannot be a function of it, so skip the pair
                # before it costs any memory.
                if dep.distinct_estimate > det.distinct_estimate:
                    continue
                pairs.append((det.name, dep_name))
                if len(pairs) >= config.INTEGRITY_MAX_PAIRS:
                    break
            if len(pairs) >= config.INTEGRITY_MAX_PAIRS:
                break

        self.pairs = pairs
        for pair in pairs:
            self.first[pair] = {}
            self.conflict_rows[pair] = []
            self.conflict_keys[pair] = set()
            self.conflict_count[pair] = 0
            self.evaluated[pair] = 0

    def accumulate(self, chunk: pd.DataFrame, rule: Rule, ctx: dict) -> None:
        if self.pairs is None:
            self._select(ctx)
        if not self.pairs:
            return

        rows = _rows_of(chunk)
        for pair in self.pairs:
            det_name, dep_name = pair
            if det_name not in chunk.columns or dep_name not in chunk.columns:
                continue

            both = present_mask(chunk[det_name]) & present_mask(chunk[dep_name])
            if not both.any():
                continue

            det_values = _values(chunk, det_name)
            dep_values = _values(chunk, dep_name)
            seen = self.first[pair]

            for idx in both[both].index:
                key = det_values.loc[idx].lower()
                value = dep_values.loc[idx]
                self.evaluated[pair] += 1

                known = seen.get(key)
                if known is None:
                    # Memory ceiling: stop learning new keys but keep
                    # checking known ones. A file with millions of distinct
                    # keys holds no functional dependency worth reporting.
                    if len(seen) < config.INTEGRITY_MAX_KEYS:
                        seen[key] = (value, int(rows.loc[idx]))
                    continue

                if value.lower() != known[0].lower():
                    self.conflict_keys[pair].add(key)
                    self.conflict_count[pair] += 1
                    if len(self.conflict_rows[pair]) < config.EXAMPLE_CAP:
                        self.conflict_rows[pair].append(
                            (int(rows.loc[idx]), det_values.loc[idx],
                             value, known[0], known[1])
                        )

    def finalise(self, rule: Rule, ctx: dict) -> tuple[int, int, list[Violation]]:
        if not self.pairs:
            return 0, 0, []

        # One determinant per dependent column. A column can look like a
        # function of several others at once, and counting each pairing
        # separately would judge the same cell repeatedly -- inflating the
        # denominator and diluting a real contradiction into insignificance.
        best: dict[str, tuple] = {}
        for pair in self.pairs:
            groups = len(self.first[pair])
            if groups < config.INTEGRITY_MIN_GROUPS:
                continue

            # Consistency is measured over ROWS, not over keys. Measured
            # over keys, a column with ten distinct values loses the whole
            # pair to a single contradiction -- the check could then never
            # report anything on any file with few distinct keys, which is
            # most of them.
            checked = self.evaluated[pair]
            conflicting = self.conflict_count[pair]
            if checked == 0:
                continue
            consistency = 1.0 - (conflicting / checked)
            if consistency < config.INTEGRITY_CONSISTENCY:
                # Not a dependency, just two unrelated columns. Excluded
                # rather than counted as failing, which would punish a file
                # for a relationship it never claimed to hold.
                continue

            dep_name = pair[1]
            rank = (consistency, groups)
            if dep_name not in best or rank > best[dep_name][0]:
                best[dep_name] = (rank, pair, conflicting)

        evaluated = 0
        failed = 0
        violations: list[Violation] = []

        for dep_name, (_, pair, conflicting) in sorted(best.items()):
            det_name = pair[0]
            evaluated += self.evaluated[pair]
            failed += conflicting

            for row, det_value, value, expected, first_row in self.conflict_rows[pair]:
                violations.append(
                    Violation(
                        row=row,
                        column=dep_name,
                        value=value,
                        reason=(
                            f"{det_name} '{det_value}' resolves to '{expected}' "
                            f"at row {first_row} but to '{value}' here"
                        ),
                    )
                )

        return evaluated, failed, violations[: config.EXAMPLE_CAP]


# --------------------------------------------------------------------------
@stateful_check("dependent_field_populated")
class DependentFieldPopulated:
    """A field left empty on the rows where its partner field is filled.

    Some fields only mean anything together: a state with its country, a
    postcode with its city. Where a file fills both on almost every row, the
    handful of rows filling only one are a broken dependency rather than
    ordinary missingness.

    This deliberately overlaps completeness, and the overlap is the point.
    Completeness asks whether a column is populated at all; this asks
    whether it is populated *where the file's own pattern requires it*. A
    column 90% full looks acceptable to completeness even when every one of
    its gaps sits on a row that needed it.
    """

    def __init__(self):
        self.pairs: list[tuple[str, str]] | None = None
        self.parent_present: dict[tuple[str, str], int] = {}
        self.both_present: dict[tuple[str, str], int] = {}
        self.gaps: dict[tuple[str, str], list[tuple[int, str]]] = {}

    def _select(self, ctx: dict) -> None:
        columns = _candidate_columns(ctx)
        pairs: list[tuple[str, str]] = []
        for parent in columns:
            for dependent in columns:
                if parent.name == dependent.name:
                    continue
                # A dependent that is already fuller than its parent can
                # never show a gap the parent does not, so testing it wastes
                # a pair slot.
                if dependent.fill_rate >= 1.0:
                    continue
                pairs.append((parent.name, dependent.name))
                if len(pairs) >= config.INTEGRITY_MAX_PAIRS:
                    break
            if len(pairs) >= config.INTEGRITY_MAX_PAIRS:
                break

        self.pairs = pairs
        for pair in pairs:
            self.parent_present[pair] = 0
            self.both_present[pair] = 0
            self.gaps[pair] = []

    def accumulate(self, chunk: pd.DataFrame, rule: Rule, ctx: dict) -> None:
        if self.pairs is None:
            self._select(ctx)
        if not self.pairs:
            return

        rows = _rows_of(chunk)
        for pair in self.pairs:
            parent_name, dependent_name = pair
            if parent_name not in chunk.columns or dependent_name not in chunk.columns:
                continue

            parent_mask = present_mask(chunk[parent_name])
            dependent_mask = present_mask(chunk[dependent_name])
            if not parent_mask.any():
                continue

            missing = parent_mask & ~dependent_mask
            self.parent_present[pair] += int(parent_mask.sum())
            self.both_present[pair] += int((parent_mask & dependent_mask).sum())

            if missing.any() and len(self.gaps[pair]) < config.EXAMPLE_CAP:
                parent_values = _values(chunk, parent_name)
                remaining = config.EXAMPLE_CAP - len(self.gaps[pair])
                for idx in missing[missing].index[:remaining]:
                    self.gaps[pair].append(
                        (int(rows.loc[idx]), parent_values.loc[idx])
                    )

    def finalise(self, rule: Rule, ctx: dict) -> tuple[int, int, list[Violation]]:
        if not self.pairs:
            return 0, 0, []

        support = rule.params.get("support", config.INTEGRITY_DEPENDENCY_SUPPORT)

        # One parent per dependent column, for the same reason as above: a
        # column that pairs with ten others would otherwise have each of its
        # gaps counted ten times over.
        best: dict[str, tuple] = {}
        for pair in self.pairs:
            seen = self.parent_present[pair]
            if seen < config.INTEGRITY_MIN_ROWS:
                continue

            rate = self.both_present[pair] / seen
            if rate < support:
                # The two fields are not kept together in this file, so
                # there is no dependency here to violate.
                continue

            dependent_name = pair[1]
            if dependent_name not in best or rate > best[dependent_name][0]:
                best[dependent_name] = (rate, pair, seen)

        evaluated = 0
        failed = 0
        violations: list[Violation] = []

        for dependent_name, (rate, pair, seen) in sorted(best.items()):
            parent_name = pair[0]
            evaluated += seen
            failed += seen - self.both_present[pair]

            for row, parent_value in self.gaps[pair]:
                violations.append(
                    Violation(
                        row=row,
                        column=dependent_name,
                        value="(blank)",
                        reason=(
                            f"{dependent_name} is empty although {parent_name} "
                            f"is populated with '{parent_value}', and the two "
                            f"are filled together on "
                            f"{rate:.1%} of rows"
                        ),
                    )
                )

        return evaluated, failed, violations[: config.EXAMPLE_CAP]


# --------------------------------------------------------------------------
def is_assessable(profile) -> tuple[bool, str]:
    """Can integrity be assessed for this dataset at all?

    Mirrors timeliness.is_assessable. The reason is shown to the user
    verbatim through `notAssessed`, so it is written for a person and says
    what is missing rather than that a check failed.
    """
    if profile.column_count < 2:
        return False, (
            "Integrity describes how fields relate to one another, and this "
            "file has only one column."
        )

    cdes = [c for c in profile.cde_columns if c.fill_rate > 0.0]
    if len(cdes) < 2:
        return False, (
            "Integrity compares fields against one another, and fewer than "
            "two populated critical data elements were found in this file."
        )

    if profile.row_count < config.INTEGRITY_MIN_ROWS:
        return False, (
            f"Integrity infers relationships from repetition, and this file "
            f"has fewer than {config.INTEGRITY_MIN_ROWS} rows to infer from."
        )

    return True, ""
