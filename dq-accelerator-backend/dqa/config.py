"""Central configuration for the DQ Accelerator.

Everything tunable lives here so that a reviewer can find and change the
knobs without reading the engine.
"""
from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Dimensions
# --------------------------------------------------------------------------
# These six keys are the contract with the frontend (API_CONTRACT.md).
# They must match src/api/constants.js on the frontend exactly.
DIMENSIONS: list[str] = [
    "completeness",
    "validity",
    "uniqueness",
    "consistency",
    "accuracy",
    "timeliness",
]

# ---------------------------------------------------------------------------
# DEFERRED: "integrity" is the seventh dimension.
#
# It is NOT implemented in this phase and must NOT be added to DIMENSIONS
# until the work below is done, because emitting an unimplemented key would
# render a permanently blank tile in the UI.
#
# Integrity measures relationships BETWEEN datasets -- orphaned foreign keys,
# missing mandatory parents, lookup codes that do not resolve. A single
# standalone CSV has no second dataset to relate to, so the dimension would
# score vacuously and mislead a client reading the report.
#
# IMPLEMENT IN THE NEXT PHASE, once multi-table sources (Salesforce
# Account-to-Contact, Oracle foreign keys) are connected. To do so:
#
#   1. Append "integrity" to DIMENSIONS above.
#   2. Extend RunContext (dqa/models.py) to hold more than one dataset --
#      this is the only structural change required; everything else is
#      already in place.
#   3. Uncomment the integrity section of rules/default_pack.yaml.
#   4. Implement dqa/checks/integrity.py using the existing stateful-check
#      interface (see dqa/checks/uniqueness.py for the pattern).
#   5. Move the rules tagged `move_to: integrity` in the rule pack out of
#      accuracy and into integrity. They are cross-field dependency checks
#      that belong to integrity but have been placed under accuracy for this
#      phase so they are not lost.
#   6. Tell the frontend to add the key to constants.js (already flagged in
#      API_CONTRACT.md).
#
# Intended integrity checks: orphaned foreign keys; mandatory parent with no
# child; cross-field dependency violations; unresolved lookup codes;
# cardinality violations on declared one-to-one relationships.
# ---------------------------------------------------------------------------
DEFERRED_DIMENSIONS: list[str] = ["integrity"]

SEVERITY_WEIGHTS: dict[str, int] = {"high": 3, "medium": 2, "low": 1}

# --------------------------------------------------------------------------
# Run stages -- `stage` is shown to the user verbatim, `stageCount` is 4.
# --------------------------------------------------------------------------
STAGES: list[str] = ["Ingesting", "Profiling", "Evaluating", "Scoring"]

# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------
MAX_UPLOAD_BYTES = 500 * 1024 * 1024        # 500 MB, returns 413
MAX_ROWS_FULL = 5_000_000                   # beyond this the file is sampled
SAMPLE_TARGET_ROWS = 1_000_000              # rows kept when sampling
CHUNK_SIZE = 50_000                         # streaming chunk size
EXAMPLE_CAP = 200                           # violations captured per rule
PROFILE_SAMPLE_VALUES = 10_000              # values sampled for type inference
TOP_VALUES_KEPT = 25
SAMPLE_VALUES_SHOWN = 3

ACCEPTED_EXTENSIONS = {".csv", ".tsv", ".txt"}
ACCEPTED_CONTENT_TYPES = {
    "text/csv",
    "text/plain",
    "text/tab-separated-values",
    "application/csv",
    "application/vnd.ms-excel",
    "application/octet-stream",
    "",
}

# --------------------------------------------------------------------------
# CDE detection
# --------------------------------------------------------------------------
CDE_THRESHOLD = 0.5
CDE_WEIGHTS = {
    "business_name_match": 0.35,
    "metadata_name_match": -0.60,
    "business_semantic_type": 0.20,
    "healthy_fill_rate": 0.15,
    "high_distinctness": 0.15,
    "single_value": -0.50,
    "very_low_distinctness": -0.25,
}

# --------------------------------------------------------------------------
# Detection thresholds
# --------------------------------------------------------------------------
TYPE_INFERENCE_AGREEMENT = 0.95     # share of values that must match a type
SEMANTIC_MATCH_RATE = 0.80          # share of values matching a semantic type
PATTERN_COVERAGE = 0.95             # mask coverage defining "normal" format
ENUM_MAX_CARDINALITY = 25           # distinct count below which a column is an enum
ENUM_COVERAGE = 0.95
FUZZY_THRESHOLD = 88                # RapidFuzz token_set_ratio, 0-100
FUZZY_MAX_BLOCK_SIZE = 500          # blocks larger than this are skipped as noise
MAD_Z_THRESHOLD = 3.5               # modified z-score for outlier detection
STALENESS_DAYS = 730                # records older than this are stale
DATE_MIN_YEAR = 1900
DATE_MAX_YEARS_AHEAD = 10

# --------------------------------------------------------------------------
# Values treated as "missing" even though they are technically present.
# --------------------------------------------------------------------------
PLACEHOLDER_VALUES: set[str] = {
    "n/a", "na", "n.a.", "null", "none", "nil", "unknown", "unspecified",
    "tbd", "tba", "xxx", "xx", "-", "--", ".", "?", "??", "test", "testing",
    "no value", "not available", "not applicable", "missing", "#n/a",
    "#null!", "#ref!", "#value!", "0000-00-00", "01/01/1900",
}

# --------------------------------------------------------------------------
# Column name patterns
# --------------------------------------------------------------------------
METADATA_NAME_PATTERNS = [
    r"^created?[_\s-]?(by|date|at|on|ts|time|stamp)",
    r"^(last[_\s-]?)?(modified|updated|changed)([_\s-]?(by|date|at|on|ts))?",
    r"^(sys|system)[_\s-]?mod[_\s-]?stamp$",
    r"^source[_\s-]?(system|file|id)$",
    r"^etl[_\s-]",
    r"^batch[_\s-]?(id|no|number)$",
    r"^(is)?[_\s-]?deleted$",
    r"^owner[_\s-]?id$",
    r"^record[_\s-]?type[_\s-]?id$",
    r"^(row|line)[_\s-]?(num|number|id)$",
    r"^_+",
    r"[_\s-]ts$",
    r"^load[_\s-]?(date|time|id)$",
    r"^version$",
    r"^checksum$",
    r"^(dw|dwh|stg|tmp)[_\s-]",
]

BUSINESS_NAME_PATTERNS = [
    r"name", r"email", r"e[_\s-]?mail", r"phone", r"mobile", r"contact",
    r"address", r"street", r"city", r"state", r"province", r"country",
    r"zip", r"postal", r"postcode", r"ssn", r"tax[_\s-]?id", r"account",
    r"customer", r"client", r"vendor", r"supplier", r"product", r"sku",
    r"amount", r"price", r"cost", r"revenue", r"salary", r"quantity",
    r"birth", r"dob", r"gender", r"title", r"company", r"organi[sz]ation",
    # Date columns carry business meaning (signup, activity, expiry) and
    # must be selectable as CDEs, or the timeliness dimension can never be
    # assessed. Audit dates are excluded by the metadata patterns above,
    # which are tested first.
    r"date$", r"^date", r"signup", r"activity", r"expir", r"due$",
    r"start", r"end$", r"renewal", r"close",
    r"^id$", r"[_\s-]id$", r"number", r"code", r"status", r"category",
    r"department", r"industry", r"currency", r"invoice", r"order",
]

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
DATA_ROOT = Path(os.environ.get("DQA_DATA_ROOT", "./data")).resolve()
RUNS_DIR = DATA_ROOT / "runs"
DB_PATH = DATA_ROOT / "runs.db"
RULE_PACK_PATH = Path(
    os.environ.get("DQA_RULE_PACK", Path(__file__).parent.parent / "rules" / "default_pack.yaml")
).resolve()

MAX_WORKERS = int(os.environ.get("DQA_MAX_WORKERS", "2"))
RETENTION_DAYS = int(os.environ.get("DQA_RETENTION_DAYS", "7"))


def ensure_dirs() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
