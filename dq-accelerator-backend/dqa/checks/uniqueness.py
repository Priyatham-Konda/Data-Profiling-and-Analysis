"""Uniqueness checks.

Three layers, cheapest first: exact record duplicates, exact duplicates on an
inferred candidate key, then fuzzy near-duplicates.

The fuzzy layer is the one that demonstrates value in a client meeting, and
it is also the one that will melt a laptop if written naively. 128,000
records compared pairwise is 8.2 billion comparisons. BLOCKING is what makes
it tractable: only records sharing a cheap blocking key are ever compared,
which turns the problem roughly linear.

These are stateful checks because no verdict is possible until the whole
dataset has been seen.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

import pandas as pd

from .. import config
from ..models import Rule, Violation
from ..rules.registry import stateful_check

ROW_COL = "__row__"
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalise(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return _NON_ALNUM.sub(" ", str(value).lower()).strip()


class _UnionFind:
    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def clusters(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for node in self.parent:
            out[self.find(node)].append(node)
        return {root: members for root, members in out.items() if len(members) > 1}


# --------------------------------------------------------------------------
@stateful_check("exact_record_duplicate")
class ExactRecordDuplicate:
    """Whole-record duplicates across the CDE columns."""

    def __init__(self):
        self.seen: dict[str, int] = {}
        self.duplicates: list[tuple[int, int, str]] = []
        self.total = 0

    def accumulate(self, chunk: pd.DataFrame, rule: Rule, ctx: dict) -> None:
        columns = _cde_columns(ctx, chunk)
        if not columns:
            return
        rows = chunk[ROW_COL] if ROW_COL in chunk.columns else pd.Series(chunk.index)

        joined = chunk[columns].fillna("").astype(str).apply(
            lambda r: "\x1f".join(v.strip().lower() for v in r), axis=1
        )
        for idx, key in joined.items():
            self.total += 1
            if not key.replace("\x1f", "").strip():
                continue
            digest = hashlib.blake2b(key.encode("utf-8"), digest_size=16).hexdigest()
            row_no = int(rows.loc[idx])
            if digest in self.seen:
                self.duplicates.append((row_no, self.seen[digest], key))
            else:
                self.seen[digest] = row_no

    def finalise(self, rule: Rule, ctx: dict) -> tuple[int, int, list[Violation]]:
        violations = [
            Violation(
                row=row,
                column=None,
                value=_preview(key),
                reason=f"Identical to row {first} across all assessed columns",
            )
            for row, first, key in self.duplicates[: config.EXAMPLE_CAP]
        ]
        return self.total, len(self.duplicates), violations


# --------------------------------------------------------------------------
@stateful_check("candidate_key_duplicate")
class CandidateKeyDuplicate:
    """Duplicates on a column that ought to be unique.

    The key is inferred: a CDE column whose distinct ratio is at or above the
    configured threshold is behaving like an identifier, so the rows that
    break it are the finding.
    """

    def __init__(self):
        self.seen: dict[str, int] = {}
        self.duplicates: list[tuple[int, int, str]] = []
        self.total = 0
        self.key_column: str | None = None

    def accumulate(self, chunk: pd.DataFrame, rule: Rule, ctx: dict) -> None:
        if self.key_column is None:
            self.key_column = _infer_key_column(ctx, rule)
        if not self.key_column or self.key_column not in chunk.columns:
            return

        rows = chunk[ROW_COL] if ROW_COL in chunk.columns else pd.Series(chunk.index)
        values = chunk[self.key_column].fillna("").astype(str).str.strip().str.lower()
        for idx, value in values.items():
            if not value:
                continue
            self.total += 1
            row_no = int(rows.loc[idx])
            if value in self.seen:
                self.duplicates.append((row_no, self.seen[value], value))
            else:
                self.seen[value] = row_no

    def finalise(self, rule: Rule, ctx: dict) -> tuple[int, int, list[Violation]]:
        violations = [
            Violation(
                row=row,
                column=self.key_column,
                value=value[:200],
                reason=f"Duplicate of row {first} on {self.key_column}, "
                       f"which is otherwise unique",
            )
            for row, first, value in self.duplicates[: config.EXAMPLE_CAP]
        ]
        return self.total, len(self.duplicates), violations


# --------------------------------------------------------------------------
@stateful_check("fuzzy_duplicate")
class FuzzyDuplicate:
    """Near-duplicate entities: 'Jon Smith' and 'John Smith' at one address.

    Blocking keys are built from whichever identifying columns exist:
      - normalised email local part
      - last 7 digits of a normalised phone
      - first 3 characters of a name plus its metaphone code

    Only records sharing a block are compared. Blocks larger than
    FUZZY_MAX_BLOCK_SIZE are skipped: a block of 10,000 records means the
    blocking key carried no information (usually a default value), and
    comparing it would cost more than it is worth.
    """

    def __init__(self):
        self.records: dict[int, str] = {}       # row -> comparison string
        self.blocks: dict[str, list[int]] = defaultdict(list)
        self.total = 0
        self.columns_used: list[str] = []

    def accumulate(self, chunk: pd.DataFrame, rule: Rule, ctx: dict) -> None:
        profile = ctx.get("profile")
        if profile is None:
            return
        if not self.columns_used:
            self.columns_used = _identity_columns(profile, chunk)
        if not self.columns_used:
            return

        rows = chunk[ROW_COL] if ROW_COL in chunk.columns else pd.Series(chunk.index)
        subset = chunk[self.columns_used].fillna("").astype(str)

        for idx, record in subset.iterrows():
            row_no = int(rows.loc[idx])
            parts = [_normalise(v) for v in record.tolist()]
            comparison = " ".join(p for p in parts if p)
            if not comparison:
                continue
            self.total += 1
            self.records[row_no] = comparison
            for key in self._blocking_keys(profile, record):
                self.blocks[key].append(row_no)

    def _blocking_keys(self, profile, record: pd.Series) -> list[str]:
        keys: list[str] = []
        for column, value in record.items():
            col_profile = profile.by_name(column)
            if col_profile is None or not value or not str(value).strip():
                continue
            semantic = col_profile.semantic_type
            text = str(value).strip().lower()

            if semantic == "email" and "@" in text:
                keys.append("e:" + text.split("@")[0])
            elif semantic == "phone":
                digits = re.sub(r"\D", "", text)
                if len(digits) >= 7:
                    keys.append("p:" + digits[-7:])
            elif semantic in ("person_name", "org_name"):
                norm = _normalise(text)
                if len(norm) >= 3:
                    keys.append("n:" + norm[:3] + ":" + _metaphone(norm))
        return keys

    def finalise(self, rule: Rule, ctx: dict) -> tuple[int, int, list[Violation]]:
        threshold = int(rule.params.get("threshold", config.FUZZY_THRESHOLD))
        max_block = int(rule.params.get("max_block_size", config.FUZZY_MAX_BLOCK_SIZE))

        try:
            from rapidfuzz import fuzz
        except ImportError:
            return self.total, 0, []

        uf = _UnionFind()
        compared: set[tuple[int, int]] = set()

        for key, members in self.blocks.items():
            if len(members) < 2 or len(members) > max_block:
                continue
            unique_members = sorted(set(members))
            for i, a in enumerate(unique_members):
                for b in unique_members[i + 1:]:
                    pair = (a, b)
                    if pair in compared:
                        continue
                    compared.add(pair)
                    score = fuzz.token_set_ratio(self.records[a], self.records[b])
                    if score >= threshold:
                        uf.union(a, b)

        clusters = uf.clusters()
        # Every member beyond the first in a cluster is a duplicate.
        failed = sum(len(members) - 1 for members in clusters.values())

        violations: list[Violation] = []
        for members in clusters.values():
            if len(violations) >= config.EXAMPLE_CAP:
                break
            ordered = sorted(members)
            keeper = ordered[0]
            for row in ordered[1:]:
                if len(violations) >= config.EXAMPLE_CAP:
                    break
                violations.append(
                    Violation(
                        row=row,
                        column=None,
                        value=_preview(self.records.get(row, "")),
                        reason=f"Probable duplicate of row {keeper} "
                               f"({', '.join(self.columns_used)})",
                    )
                )
        return self.total, failed, violations


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _metaphone(text: str) -> str:
    try:
        from metaphone import doublemetaphone

        primary, _ = doublemetaphone(text)
        return primary or text[:4]
    except ImportError:
        # Crude consonant skeleton, adequate as a blocking key.
        return re.sub(r"[aeiou]", "", text)[:4]


def _cde_columns(ctx: dict, chunk: pd.DataFrame) -> list[str]:
    profile = ctx.get("profile")
    if profile is None:
        return []
    return [c.name for c in profile.cde_columns if c.name in chunk.columns]


def _identity_columns(profile, chunk: pd.DataFrame) -> list[str]:
    wanted = {"person_name", "org_name", "email", "phone", "address"}
    columns = [
        c.name
        for c in profile.cde_columns
        if c.semantic_type in wanted and c.name in chunk.columns
    ]
    return columns[:6]


def _infer_key_column(ctx: dict, rule: Rule) -> str | None:
    profile = ctx.get("profile")
    if profile is None:
        return None
    threshold = float(rule.params.get("distinctness", 0.95))
    candidates = [
        c for c in profile.cde_columns
        if c.distinct_ratio >= threshold and c.fill_rate > 0.5
    ]
    if not candidates:
        return None
    identifiers = [c for c in candidates if c.semantic_type == "identifier"]
    chosen = identifiers[0] if identifiers else candidates[0]
    return chosen.name


def _preview(text: str) -> str:
    cleaned = str(text).replace("\x1f", " | ").strip()
    return cleaned[:200] if cleaned else "(blank)"
