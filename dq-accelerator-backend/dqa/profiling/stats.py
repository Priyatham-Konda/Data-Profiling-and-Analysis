"""Streaming column profiling.

Accumulates per-column statistics chunk by chunk so that a 5 million row file
never needs to be held in memory at once. Distinct-value tracking is capped
and falls back to an estimate beyond the cap.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

import pandas as pd

from .. import config
from ..models import ColumnProfile, DatasetProfile
from .semantic import detect_semantic_type

# --------------------------------------------------------------------------
# Type inference
# --------------------------------------------------------------------------
INT_RE = re.compile(r"^-?[\d,]+$")
DEC_RE = re.compile(r"^-?[\d,]*\.\d+$|^-?[\d,]+\.?\d*$")
BOOL_VALUES = {"true", "false", "yes", "no", "y", "n", "t", "f", "1", "0"}

DATE_FORMATS = [
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y",
    "%Y/%m/%d", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y",
    "%Y%m%d", "%d-%b-%Y", "%d-%b-%y", "%m/%d/%y", "%d/%m/%y",
]
DATETIME_HINT = re.compile(r"\d{1,2}:\d{2}")

DISTINCT_CAP = 50_000


def _clean_numeric(value: str) -> str:
    return value.replace(",", "").replace(" ", "").lstrip("$\u20ac\u00a3\u20b9")


def try_parse_dates(values: Iterable[str]) -> tuple[float, list[str]]:
    """Return (parse success rate, list of formats that matched)."""
    vals = [v for v in values if isinstance(v, str) and v.strip()][:2000]
    if not vals:
        return 0.0, []
    matched_formats: Counter = Counter()
    parsed = 0
    for v in vals:
        v = v.strip()
        for fmt in DATE_FORMATS:
            try:
                pd.to_datetime(v, format=fmt)
                matched_formats[fmt] += 1
                parsed += 1
                break
            except (ValueError, TypeError):
                continue
        else:
            # Last resort: let dateutil try, but only for plausible strings.
            if len(v) >= 6 and any(c.isdigit() for c in v):
                try:
                    pd.to_datetime(v, errors="raise")
                    matched_formats["mixed"] += 1
                    parsed += 1
                except Exception:
                    pass
    return parsed / len(vals), [f for f, _ in matched_formats.most_common()]


def infer_type(values: list[str]) -> str:
    vals = [v.strip() for v in values if isinstance(v, str) and v.strip()]
    if not vals:
        return "string"
    n = len(vals)
    threshold = config.TYPE_INFERENCE_AGREEMENT

    if sum(1 for v in vals if v.lower() in BOOL_VALUES) / n >= threshold:
        # All-numeric 0/1 columns are integers, not booleans, unless the
        # column only ever holds 0 and 1 and is named like a flag. Keep it
        # simple: treat pure 0/1 as integer and let the name decide later.
        if not all(v in {"0", "1"} for v in vals):
            return "boolean"

    ints = sum(1 for v in vals if INT_RE.match(v) and _clean_numeric(v).lstrip("-").isdigit())
    if ints / n >= threshold:
        return "integer"

    decs = 0
    for v in vals:
        try:
            float(_clean_numeric(v))
            decs += 1
        except ValueError:
            continue
    if decs / n >= threshold:
        return "decimal"

    date_rate, _ = try_parse_dates(vals)
    if date_rate >= threshold:
        if any(DATETIME_HINT.search(v) for v in vals[:200]):
            return "datetime"
        return "date"

    return "string"


# --------------------------------------------------------------------------
# Pattern masking -- the backbone of the consistency dimension
# --------------------------------------------------------------------------
def pattern_mask(value: str) -> str:
    """Collapse a value into a format signature.

    'CUS-00194'   -> 'A-9'
    'john@x.com'  -> 'a@a.a'
    '(555) 12-34' -> '(9) 9-9'

    Runs collapse so that length variation does not explode the histogram.
    """
    if not isinstance(value, str):
        return ""
    out: list[str] = []
    prev = ""
    for ch in value.strip():
        if ch.isdigit():
            cur = "9"
        elif ch.isupper():
            cur = "A"
        elif ch.islower():
            cur = "a"
        elif ch.isspace():
            cur = " "
        else:
            cur = ch
        if cur != prev or cur not in {"9", "A", "a", " "}:
            out.append(cur)
        prev = cur
    return "".join(out)


# --------------------------------------------------------------------------
# Accumulator
# --------------------------------------------------------------------------
class ColumnAccumulator:
    def __init__(self, name: str, position: int):
        self.profile = ColumnProfile(name=name, position=position)
        self._distinct: set[str] = set()
        self._value_counts: Counter = Counter()
        self._masks: Counter = Counter()
        self._sample: list[str] = []
        self._lengths_sum = 0
        self._lengths_n = 0
        self._numeric_values: list[float] = []

    def update(self, series: pd.Series) -> None:
        p = self.profile
        p.total += len(series)

        isna = series.isna()
        p.nulls += int(isna.sum())

        present = series[~isna].astype(str)
        stripped = present.str.strip()
        blanks = stripped == ""
        p.blanks += int(blanks.sum())

        values = stripped[~blanks]
        if values.empty:
            return

        lowered = values.str.lower()
        p.placeholders += int(lowered.isin(config.PLACEHOLDER_VALUES).sum())

        # Distinct tracking, capped.
        if not self.profile.distinct_capped:
            self._distinct.update(values.tolist())
            if len(self._distinct) > DISTINCT_CAP:
                self.profile.distinct_capped = True

        self._value_counts.update(values.tolist()[:20_000])

        for v in values.tolist()[:5_000]:
            self._masks[pattern_mask(v)] += 1

        lengths = values.str.len()
        self._lengths_sum += int(lengths.sum())
        self._lengths_n += len(lengths)
        lo, hi = int(lengths.min()), int(lengths.max())
        p.min_length = lo if p.min_length == 0 else min(p.min_length, lo)
        p.max_length = max(p.max_length, hi)

        if len(self._sample) < config.PROFILE_SAMPLE_VALUES:
            need = config.PROFILE_SAMPLE_VALUES - len(self._sample)
            self._sample.extend(values.tolist()[:need])

    def finalise(self) -> ColumnProfile:
        p = self.profile
        p.distinct_estimate = (
            DISTINCT_CAP if p.distinct_capped else len(self._distinct)
        )
        p.top_values = self._value_counts.most_common(config.TOP_VALUES_KEPT)
        p.pattern_masks = dict(self._masks.most_common(50))
        p.sample_values = self._sample[: config.SAMPLE_VALUES_SHOWN]
        p.mean_length = (
            self._lengths_sum / self._lengths_n if self._lengths_n else 0.0
        )

        p.inferred_type = infer_type(self._sample)
        p.date_parse_rate, _ = try_parse_dates(self._sample)

        p.semantic_type, p.semantic_confidence, p.name_semantic_conflict = (
            detect_semantic_type(p.name, self._sample, p.inferred_type)
        )

        # Enum domain: low cardinality with high coverage.
        #
        # Open-ended types are excluded regardless of how few distinct
        # values a particular file happens to contain. A sample with 20
        # surnames across 500 rows looks like a closed domain arithmetically,
        # but surnames are not a domain and flagging the 21st as invalid
        # would be a serious false positive in front of a client.
        open_ended = {"person_name", "org_name", "email", "phone", "address",
                      "identifier", "url", "free_text", "amount", "postcode"}
        if (0 < p.distinct_estimate <= config.ENUM_MAX_CARDINALITY
                and not p.distinct_capped
                and p.semantic_type not in open_ended):
            total_counted = sum(self._value_counts.values())
            if total_counted:
                # The domain is the SMALLEST set of values covering
                # ENUM_COVERAGE of the column. Taking every observed value
                # would include the rare stragglers this check exists to
                # find, and the rule could never fire.
                domain, running = [], 0
                for value, count in self._value_counts.most_common():
                    domain.append(value)
                    running += count
                    if running / total_counted >= config.ENUM_COVERAGE:
                        break
                if domain:
                    p.is_enum = True
                    p.enum_domain = domain

        # Numeric statistics for outlier detection.
        if p.inferred_type in ("integer", "decimal"):
            nums = []
            for v in self._sample:
                try:
                    nums.append(float(_clean_numeric(v)))
                except (ValueError, AttributeError):
                    continue
            if nums:
                s = pd.Series(nums)
                median = float(s.median())
                mad = float((s - median).abs().median())
                p.numeric_stats = {
                    "min": float(s.min()),
                    "max": float(s.max()),
                    "mean": float(s.mean()),
                    "median": median,
                    "mad": mad,
                    "negatives": int((s < 0).sum()),
                }
                p.min_value = str(s.min())
                p.max_value = str(s.max())
        else:
            sorted_vals = sorted(self._distinct)[:1] if self._distinct else []
            if sorted_vals:
                p.min_value = sorted_vals[0]
                p.max_value = max(self._distinct)

        return p


class DatasetProfiler:
    def __init__(self, columns: list[str]):
        self.accumulators = {
            name: ColumnAccumulator(name, i) for i, name in enumerate(columns)
        }

    def update(self, chunk: pd.DataFrame) -> None:
        for name, acc in self.accumulators.items():
            if name in chunk.columns:
                acc.update(chunk[name])

    def finalise(self, row_count: int) -> DatasetProfile:
        profiles = [acc.finalise() for acc in self.accumulators.values()]
        return DatasetProfile(
            row_count=row_count,
            column_count=len(profiles),
            columns=profiles,
        )
