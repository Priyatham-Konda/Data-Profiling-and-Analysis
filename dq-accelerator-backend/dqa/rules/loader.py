"""Rule pack loading and expansion.

A rule in YAML is a template. Loading validates it; expansion binds it to the
concrete columns of one dataset, producing one executable Rule per column.

Rules are configuration, not code. This is the single design decision that
makes the later phases additive: client-specific rule packs (phase 2) and
agent-generated packs (phase 3) need no engine change at all.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .. import config
from ..models import ColumnProfile, DatasetProfile, Rule
from .registry import is_registered

VALID_SEVERITIES = set(config.SEVERITY_WEIGHTS)


class RulePackError(Exception):
    """Malformed rule pack. Fail loudly at load, never silently at runtime."""


REQUIRED_FIELDS = {"id", "name", "dimension", "check"}


def load_pack(path: str | Path | None = None) -> list[Rule]:
    path = Path(path or config.RULE_PACK_PATH)
    if not path.exists():
        raise RulePackError(f"Rule pack not found at {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RulePackError(f"Rule pack is not valid YAML: {exc}") from exc

    entries = raw.get("rules")
    if not isinstance(entries, list) or not entries:
        raise RulePackError("Rule pack contains no 'rules' list")

    rules: list[Rule] = []
    seen_ids: set[str] = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RulePackError(f"Rule at position {i} is not a mapping")
        missing = REQUIRED_FIELDS - entry.keys()
        if missing:
            raise RulePackError(
                f"Rule at position {i} is missing: {', '.join(sorted(missing))}"
            )
        rid = str(entry["id"])
        if rid in seen_ids:
            raise RulePackError(f"Duplicate rule id: {rid}")
        seen_ids.add(rid)

        dimension = entry["dimension"]
        if dimension not in config.DIMENSIONS:
            if dimension in config.DEFERRED_DIMENSIONS:
                raise RulePackError(
                    f"Rule {rid} targets '{dimension}', which is deferred. "
                    f"See DEFERRED_DIMENSIONS in dqa/config.py."
                )
            raise RulePackError(f"Rule {rid} has unknown dimension '{dimension}'")

        check = entry["check"]
        if not is_registered(check):
            raise RulePackError(f"Rule {rid} references unknown check '{check}'")

        severity = entry.get("severity", "medium")
        if severity not in VALID_SEVERITIES:
            raise RulePackError(f"Rule {rid} has unknown severity '{severity}'")

        rules.append(
            Rule(
                id=rid,
                name=entry["name"],
                dimension=dimension,
                check=check,
                severity=severity,
                params=entry.get("params") or {},
                applies_to=entry.get("applies_to") or {},
                message=entry.get("message", ""),
                move_to=entry.get("move_to"),
            )
        )
    return rules


# --------------------------------------------------------------------------
# Expansion
# --------------------------------------------------------------------------
def _column_matches(rule: Rule, column: ColumnProfile) -> bool:
    spec = rule.applies_to or {}

    if spec.get("scope") == "record":
        return True

    if spec.get("cde_only", True) and not column.is_cde:
        return False

    semantic = spec.get("semantic_type")
    if semantic:
        wanted = semantic if isinstance(semantic, list) else [semantic]
        if column.semantic_type not in wanted:
            return False

    inferred = spec.get("inferred_type")
    if inferred:
        wanted = inferred if isinstance(inferred, list) else [inferred]
        if column.inferred_type not in wanted:
            return False

    pattern = spec.get("name_matches")
    if pattern and not re.search(pattern, column.name, re.I):
        return False

    if spec.get("enum_only") and not column.is_enum:
        return False

    min_fill = spec.get("min_fill_rate")
    if min_fill is not None and column.fill_rate < float(min_fill):
        return False

    return True


def expand(rules: list[Rule], profile: DatasetProfile) -> list[Rule]:
    """Bind template rules to the concrete columns of this dataset."""
    expanded: list[Rule] = []
    for rule in rules:
        if (rule.applies_to or {}).get("scope") == "record":
            bound = Rule(
                id=rule.id,
                name=rule.name,
                dimension=rule.dimension,
                check=rule.check,
                severity=rule.severity,
                params=dict(rule.params),
                applies_to=dict(rule.applies_to),
                message=rule.message,
                column=None,
                template_id=rule.id,
                move_to=rule.move_to,
            )
            expanded.append(bound)
            continue

        index = 0
        for column in profile.columns:
            if _column_matches(rule, column):
                index += 1
                expanded.append(rule.expanded(column.name, index))
    return expanded
