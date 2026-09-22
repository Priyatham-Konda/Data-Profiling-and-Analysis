"""PDF report generation.

Reads the SAME stored results the dashboard reads, so the report and the
screen can never disagree. WeasyPrint rather than a headless browser: pure
Python, BSD licensed, and no 150 MB Chromium in the image.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import config
from ..scoring.aggregate import band
from ..store import artifacts, registry

log = logging.getLogger("dqa.report")
TEMPLATE_DIR = Path(__file__).parent / "templates"

DIMENSION_BLURB = {
    "completeness": "Whether values are present in the fields that matter.",
    "validity": "Whether values conform to the format their field implies.",
    "uniqueness": "Whether the same real-world entity appears more than once.",
    "consistency": "Whether a field is formatted the same way throughout.",
    "accuracy": "Whether values are plausible and free of contradiction.",
    "timeliness": "Whether the data is current and its dates are coherent.",
}

# Business consequence per dimension, used in the executive summary. Sales
# needs the consequence, not the metric.
DIMENSION_IMPACT = {
    "completeness": "records that cannot be actioned, and campaigns that skip customers",
    "validity": "failed deliveries, rejected integrations and manual rework",
    "uniqueness": "duplicated outreach spend and a fragmented view of each customer",
    "consistency": "broken joins, unreliable grouping and inaccurate reporting",
    "accuracy": "decisions taken on wrong information",
    "timeliness": "action taken on records that no longer reflect reality",
}


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def build_context(run_id: str, report_type: str) -> dict[str, Any]:
    record = registry.get(run_id)
    results = artifacts.read_results(run_id)
    profile = artifacts.read_profile(run_id) or {}
    if record is None or results is None:
        raise ValueError("Run has no results to report on")

    scores = results.get("scores", {})
    not_assessed = results.get("notAssessed", {})
    rules = list(results.get("rules", {}).values())

    dimensions = []
    for key in config.DIMENSIONS:
        score = scores.get(key)
        dim_rules = sorted(
            [r for r in rules if r.get("dimension") == key],
            key=lambda r: r.get("passRate", 1.0),
        )
        examples = []
        if report_type == "in-depth":
            for rule in dim_rules[:3]:
                rows = artifacts.read_violations(
                    run_id, key, rule.get("rule_id", ""), limit=10
                )
                if rows:
                    examples.append({"rule": rule, "rows": rows})
        dimensions.append(
            {
                "key": key,
                "label": key.title(),
                "score": score,
                "band": band(score),
                "blurb": DIMENSION_BLURB.get(key, ""),
                "impact": DIMENSION_IMPACT.get(key, ""),
                "not_assessed": not_assessed.get(key),
                "rules": dim_rules,
                "failed": sum(r.get("failed", 0) for r in dim_rules),
                "examples": examples,
            }
        )

    assessed = [d for d in dimensions if d["score"] is not None]
    worst = sorted(assessed, key=lambda d: d["score"])[:3]

    cde_columns = [c for c in profile.get("columns", []) if c.get("is_cde")]

    return {
        "run_id": run_id,
        "filename": record["file"],
        "generated": datetime.now().strftime("%d %B %Y at %H:%M"),
        "overall": results.get("overall"),
        "overall_band": band(results.get("overall")),
        "records": results.get("records", 0),
        "columns": results.get("columns", 0),
        "cdes": results.get("cdes", 0),
        "cde_columns": cde_columns,
        "sampled": results.get("sampled", False),
        "sampled_rows": results.get("sampledRows"),
        "parse_warnings": results.get("parseWarnings", 0),
        "dimensions": dimensions,
        "worst": worst,
        "report_type": report_type,
        "is_in_depth": report_type == "in-depth",
        "deferred_note": (
            "This assessment covers six dimensions. A seventh, integrity, "
            "measures relationships between datasets and is assessed when "
            "multiple related sources are connected."
        ),
    }


def render_report(run_id: str, report_type: str = "summary") -> Path:
    context = build_context(run_id, report_type)
    html = _env().get_template("report.html.j2").render(**context)
    output = artifacts.report_path(run_id, report_type)

    try:
        from weasyprint import HTML

        HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(str(output))
    except ImportError:
        # WeasyPrint needs Pango and Cairo system libraries. Where they are
        # absent the HTML is written beside the expected PDF path so the run
        # still produces a readable artifact and the failure is obvious.
        fallback = output.with_suffix(".html")
        fallback.write_text(html, encoding="utf-8")
        log.warning("WeasyPrint unavailable; wrote HTML to %s", fallback)
        raise RuntimeError(
            "PDF rendering is unavailable because WeasyPrint is not installed. "
            "Install it with its Pango and Cairo system libraries."
        )
    return output
