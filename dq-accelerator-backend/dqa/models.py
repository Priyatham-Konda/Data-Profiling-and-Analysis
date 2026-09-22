"""Core data structures shared by the engine.

Plain dataclasses rather than pydantic models: these are internal and are
serialised by hand into the API shapes defined in API_CONTRACT.md. Pydantic
is used only at the API boundary (dqa/api/schemas.py).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------
@dataclass
class DialectInfo:
    """What was detected about the physical shape of the file."""
    encoding: str
    delimiter: str
    quotechar: str = '"'
    header_row: int = 0
    preamble_rows: int = 0
    confidence: float = 1.0
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Profiling
# --------------------------------------------------------------------------
@dataclass
class ColumnProfile:
    name: str
    position: int
    total: int = 0
    nulls: int = 0
    blanks: int = 0
    placeholders: int = 0
    distinct_estimate: int = 0
    distinct_capped: bool = False
    inferred_type: str = "string"
    semantic_type: str = "free_text"
    semantic_confidence: float = 0.0
    min_value: Optional[str] = None
    max_value: Optional[str] = None
    min_length: int = 0
    max_length: int = 0
    mean_length: float = 0.0
    top_values: list[tuple[str, int]] = field(default_factory=list)
    pattern_masks: dict[str, int] = field(default_factory=dict)
    sample_values: list[str] = field(default_factory=list)
    is_enum: bool = False
    enum_domain: list[str] = field(default_factory=list)
    numeric_stats: dict[str, float] = field(default_factory=dict)
    date_parse_rate: float = 0.0
    name_semantic_conflict: bool = False

    # CDE decision
    is_cde: bool = False
    cde_score: float = 0.0
    cde_reasons: list[str] = field(default_factory=list)

    @property
    def populated(self) -> int:
        return max(self.total - self.nulls - self.blanks, 0)

    @property
    def fill_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.populated / self.total

    @property
    def distinct_ratio(self) -> float:
        if self.populated == 0:
            return 0.0
        return min(self.distinct_estimate / self.populated, 1.0)

    def to_api(self) -> dict[str, Any]:
        """The shape returned by GET /runs/{id}/profile."""
        return {
            "name": self.name,
            "inferredType": self.inferred_type,
            "semanticType": self.semantic_type,
            "fillRate": round(self.fill_rate, 4),
            "distinctRatio": round(self.distinct_ratio, 4),
            "sampleValues": self.sample_values,
            "isCde": self.is_cde,
            "cdeScore": round(self.cde_score, 3),
            "cdeReason": "; ".join(self.cde_reasons) or "No strong signal either way",
        }


@dataclass
class DatasetProfile:
    row_count: int = 0
    column_count: int = 0
    parse_warnings: int = 0
    parse_warning_examples: list[str] = field(default_factory=list)
    sampled: bool = False
    sampled_rows: Optional[int] = None
    columns: list[ColumnProfile] = field(default_factory=list)

    def by_name(self, name: str) -> Optional[ColumnProfile]:
        for c in self.columns:
            if c.name == name:
                return c
        return None

    @property
    def cde_columns(self) -> list[ColumnProfile]:
        return [c for c in self.columns if c.is_cde]


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------
@dataclass
class Rule:
    """A declarative rule, loaded from YAML and expanded against a column."""
    id: str
    name: str
    dimension: str
    check: str
    severity: str = "medium"
    params: dict[str, Any] = field(default_factory=dict)
    applies_to: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    column: Optional[str] = None      # set when expanded against a column
    template_id: Optional[str] = None  # the un-expanded rule id
    move_to: Optional[str] = None      # migration marker, e.g. "integrity"

    def expanded(self, column: str, index: int) -> "Rule":
        return Rule(
            id=f"{self.id}-{index:02d}",
            name=f"{self.name} ({column})",
            dimension=self.dimension,
            check=self.check,
            severity=self.severity,
            params=dict(self.params),
            applies_to=dict(self.applies_to),
            message=self.message,
            column=column,
            template_id=self.id,
            move_to=self.move_to,
        )


@dataclass
class Violation:
    row: int
    column: Optional[str]
    value: str
    reason: str

    def to_api(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "column": self.column,
            "value": self.value,
            "reason": self.reason,
        }


@dataclass
class RuleResult:
    rule_id: str
    rule_name: str
    dimension: str
    severity: str
    column: Optional[str]
    evaluated: int = 0
    failed: int = 0

    @property
    def pass_rate(self) -> float:
        if self.evaluated == 0:
            return 1.0
        return 1.0 - (self.failed / self.evaluated)

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.rule_id,
            "name": self.rule_name,
            "passRate": round(self.pass_rate, 4),
            "column": self.column,
            "severity": self.severity,
            "evaluated": self.evaluated,
            "failed": self.failed,
        }


# --------------------------------------------------------------------------
# Run context
# --------------------------------------------------------------------------
@dataclass
class RunContext:
    """State passed through the pipeline for one run.

    NOTE for the integrity phase: this holds exactly one dataset. Integrity
    checks need to see two or more related datasets at once, so this class
    grows a `datasets: dict[str, DatasetProfile]` and the checks receive the
    whole map rather than a single profile. That is the one structural change
    the integrity dimension requires; see dqa/config.py for the full list.
    """
    run_id: str
    source_path: str
    original_filename: str
    dialect: Optional[DialectInfo] = None
    profile: DatasetProfile = field(default_factory=DatasetProfile)
    cancel_event: Optional[threading.Event] = None

    def cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()


def dataclass_to_dict(obj: Any) -> Any:
    return asdict(obj)
