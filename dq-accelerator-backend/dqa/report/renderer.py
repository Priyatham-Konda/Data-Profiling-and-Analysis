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
    "integrity": "Whether the relationships the file asserts hold across it.",
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
    "integrity": "records that contradict one another, and joins that silently drop rows",
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
            # Same rules, same order, same cap as the dimension table above,
            # so every failing rule a reader sees listed has its records
            # further down.
            for rule in dim_rules[: config.REPORT_RULES_SHOWN]:
                if not rule.get("failed"):
                    continue
                rows = artifacts.read_violations(
                    run_id, key, rule.get("rule_id", ""),
                    limit=config.REPORT_EXAMPLE_ROWS,
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
    # Every dimension, weakest first, rather than the three weakest. Three
    # was enough to name the headline problem but hid whether the rest of
    # the file was healthy or merely less bad. Dimensions that could not be
    # scored sort last, carrying their reason instead of a number.
    ranked = sorted(assessed, key=lambda d: d["score"])
    ranked += [d for d in dimensions if d["score"] is None]

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
        "ranked": ranked,
        "any_assessed": bool(assessed),
        "rules_shown": config.REPORT_RULES_SHOWN,
        "report_type": report_type,
        "is_in_depth": report_type == "in-depth",
        # Integrity is scored here, but only within this one file. Saying so
        # in the report matters: a reader who sees an integrity score must
        # not conclude their foreign keys were checked against other systems.
        "deferred_note": (
            "This assessment covers seven dimensions. Integrity is assessed "
            "within this file -- relationships it asserts about itself, such "
            "as a code that must always resolve to the same value. Integrity "
            "between datasets, such as foreign keys resolving against another "
            "system, is assessed when multiple related sources are connected."
        ),
    }


def render_report(run_id: str, report_type: str = "summary") -> Path:
    context = build_context(run_id, report_type)
    html = _env().get_template("report.html.j2").render(**context)
    output = artifacts.report_path(run_id, report_type)

    try:
        from weasyprint import HTML

        HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(str(output))
    except (ImportError, OSError) as exc:
        # WeasyPrint needs Pango and Cairo system libraries. Where they are
        # absent the HTML is written beside the expected PDF path so the run
        # still produces a readable artifact and the failure is obvious.
        # OSError matters as much as ImportError here: the package imports
        # cleanly and only fails when cffi dlopens libgobject, so a missing
        # Pango surfaces as OSError, not ImportError.
        fallback = output.with_suffix(".html")
        fallback.write_text(html, encoding="utf-8")
        log.warning("WeasyPrint unavailable (%s); wrote HTML to %s", exc, fallback)
        raise RuntimeError(
            "PDF rendering is unavailable: WeasyPrint could not load its Pango "
            "and Cairo system libraries. An HTML copy of the report was saved "
            f"to {fallback.name} instead."
        ) from exc
    return output
