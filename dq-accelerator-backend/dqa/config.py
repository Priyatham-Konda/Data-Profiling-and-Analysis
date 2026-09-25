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
# These seven keys are the contract with the frontend (API_CONTRACT.md).
# They must match src/api/constants.js on the frontend exactly.
#
# `integrity` joined the list in revision 3. It is assessed WITHIN a single
# uploaded file; dqa/checks/integrity.py states what that covers, and the
# note below states what cross-dataset integrity still needs.
DIMENSIONS: list[str] = [
    "completeness",
    "validity",
    "uniqueness",
    "consistency",
    "accuracy",
    "timeliness",
    "integrity",
]

# ---------------------------------------------------------------------------
# CROSS-DATASET INTEGRITY IS STILL OUTSTANDING.
#
# `integrity` is implemented and scored, but only for relationships that
# live inside one file: functional dependencies the file contradicts, and
# fields left empty where their partner field is populated. See
# dqa/checks/integrity.py.
#
# What genuinely needs a second dataset, and so a later phase:
#
#   * orphaned foreign keys -- a child row pointing at a parent that is not
#     there (INT-ORPHAN at the foot of rules/default_pack.yaml)
#   * mandatory parents carrying no child row (INT-CHILDLESS)
#   * lookup codes resolved against a separate reference table (INT-LOOKUP)
#
# To add them:
#
#   1. Extend RunContext (dqa/models.py) to hold more than one dataset.
#      This is still the only structural change required.
#   2. Uncomment the cross-dataset rules at the foot of the rule pack.
#   3. Implement their checks in dqa/checks/integrity.py against the
#      existing stateful-check interface, beside the within-dataset ones.
#
# Nothing already implemented has to change to accommodate them.
#
# DEFERRED_DIMENSIONS stays: the rule loader still rejects any rule naming a
# dimension listed here. It is empty because nothing is deferred right now.
# ---------------------------------------------------------------------------
DEFERRED_DIMENSIONS: list[str] = []

SEVERITY_WEIGHTS: dict[str, int] = {"high": 3, "medium": 2, "low": 1}

# --------------------------------------------------------------------------
# Run stages -- `stage` is shown to the user verbatim, `stageCount` is 4.
# --------------------------------------------------------------------------
STAGES: list[str] = ["Ingesting", "Profiling", "Evaluating", "Scoring"]

# --------------------------------------------------------------------------
# Run statuses -- the contract with the frontend (API_CONTRACT.md revision 4)
# --------------------------------------------------------------------------
# `awaiting_cdes` sits between Profiling and Evaluating: the pipeline stops
# once the critical data elements have been detected and waits for a person
# to confirm them. Nothing is scored until they do, so a run in this state
# carries no scores and no overall -- only the three counts Profiling
# already established.
RUN_STATUSES: list[str] = ["processing", "awaiting_cdes", "completed", "failed"]

# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------
MAX_UPLOAD_BYTES = 500 * 1024 * 1024        # 500 MB, returns 413
MAX_ROWS_FULL = 5_000_000                   # beyond this the file is sampled
SAMPLE_TARGET_ROWS = 1_000_000              # rows kept when sampling
CHUNK_SIZE = 50_000                         # streaming chunk size
EXAMPLE_CAP = 200                           # violations captured per rule

# Report rendering. One constant governs BOTH the per-dimension rule table
# and the example sections, so a rule listed as failing always has its
# records below. They disagreed before: the table listed 12 rules and the
# examples covered 3, which on a file with 33 failing completeness rules
# read as if the examples section had been dropped.
REPORT_RULES_SHOWN = 12
REPORT_EXAMPLE_ROWS = 10
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
# Integrity -- relationships within one dataset
# --------------------------------------------------------------------------
# A relationship is enforced only once the file itself demonstrates it.
# These decide what "demonstrates" means, and bound the cost of looking.
INTEGRITY_MAX_PAIRS = 24             # column pairs examined; caps quadratic growth
INTEGRITY_MAX_KEYS = 10_000          # distinct determinant values held in memory
INTEGRITY_MAX_KEY_RATIO = 0.6        # above this a column is an id, not a key
INTEGRITY_MIN_GROUPS = 5             # distinct keys needed before judging a pair
INTEGRITY_CONSISTENCY = 0.95         # share of keys that must map to one value
INTEGRITY_DEPENDENCY_SUPPORT = 0.90  # share of rows filling both fields together
INTEGRITY_MIN_ROWS = 20              # rows needed before inferring anything

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
