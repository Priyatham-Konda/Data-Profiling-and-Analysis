"""Engine tests.

These assert EXACT failure counts against fixtures with a known number of
seeded defects. A test that only asserts the code ran would not have caught
any of the eight real bugs found during the first integration run.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def data_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("dqa_data")
    os.environ["DQA_DATA_ROOT"] = str(root)

    import importlib
    from dqa import config

    importlib.reload(config)
    config.ensure_dirs()

    from dqa.store import registry

    registry.init()
    return root


@pytest.fixture(scope="session")
def manifest():
    return json.loads((FIXTURES / "manifest.json").read_text())


def _run(fixture_name: str, run_id: str, cde_override=None):
    from dqa.models import RunContext
    from dqa.runner import run_assessment
    from dqa.store import artifacts, registry

    artifacts.ensure(run_id)
    shutil.copy(FIXTURES / fixture_name, artifacts.source_path(run_id))
    registry.create(run_id, fixture_name)
    artifacts.write_meta(run_id, {"id": run_id, "file": fixture_name,
                                  "status": "processing"})
    ctx = RunContext(
        run_id=run_id,
        source_path=str(artifacts.source_path(run_id)),
        original_filename=fixture_name,
        cancel_event=threading.Event(),
    )
    return run_assessment(ctx, cde_override=cde_override)


@pytest.fixture(scope="session")
def dirty(data_root):
    return _run("dirty_known.csv", "run_dirty")


def failed(results: dict, rule_prefix: str, column: str | None = None) -> int:
    """Total failures for rules whose template id matches, optionally scoped."""
    total = 0
    for rule in results["rules"].values():
        if not rule["rule_id"].startswith(rule_prefix):
            continue
        if column is not None and rule["column"] != column:
            continue
        total += rule["failed"]
    return total


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------
class TestIngestion:
    def test_detects_semicolon_bom_and_preamble(self):
        from dqa.ingest.reader import open_source

        source = open_source(FIXTURES / "messy_format.csv")
        assert source.dialect.delimiter == ";"
        assert source.dialect.header_row == 3, "3 preamble rows must be skipped"
        assert "customer_id" in source.columns
        assert not source.columns[0].startswith("\ufeff"), "BOM must be stripped"

    def test_ragged_rows_warn_but_do_not_fail(self, data_root):
        results = _run("ragged.csv", "run_ragged")
        assert results["parseWarnings"] == 12
        assert results["overall"] is not None, "a ragged file must still complete"

    def test_single_column_file_is_handled(self, data_root):
        results = _run("single_column.csv", "run_single")
        assert results["columns"] == 1
        assert results["overall"] is not None

    def test_row_numbers_point_at_the_original_file(self, dirty):
        from dqa.store import artifacts

        rows = artifacts.read_violations("run_dirty", "completeness",
                                         "COM-NULL-04", limit=5)
        assert rows, "expected captured violations"
        # Header is line 1, so the first data row is line 2.
        assert all(r["row"] >= 2 for r in rows)


# --------------------------------------------------------------------------
# Profiling and CDE detection
# --------------------------------------------------------------------------
class TestProfiling:
    def test_metadata_columns_are_not_cdes(self, dirty):
        cdes = set(dirty["cdeColumns"])
        for column in ("created_by", "created_date", "source_system",
                       "record_version"):
            assert column not in cdes, f"{column} is a metadata column"

    def test_business_columns_are_cdes(self, dirty):
        cdes = set(dirty["cdeColumns"])
        for column in ("email", "phone", "customer_id", "country",
                       "signup_date", "last_activity_date"):
            assert column in cdes, f"{column} should be critical"

    def test_cde_share_is_in_the_expected_band(self, dirty):
        share = dirty["cdes"] / dirty["columns"]
        assert 0.25 <= share <= 0.85, f"CDE share {share:.0%} looks wrong"

    def test_semantic_types_detected(self, data_root):
        from dqa.store import artifacts

        profile = artifacts.read_profile("run_dirty")
        types = {c["name"]: c["semantic_type"] for c in profile["columns"]}
        assert types["email"] == "email"
        assert types["phone"] == "phone"
        assert types["signup_date"] in ("date", "datetime")
        assert types["country"] == "country"

    def test_names_are_not_treated_as_an_enum(self, data_root):
        """20 surnames in 500 rows is not a closed domain."""
        from dqa.store import artifacts

        profile = artifacts.read_profile("run_dirty")
        by_name = {c["name"]: c for c in profile["columns"]}
        assert not by_name["last_name"]["is_enum"]
        assert by_name["account_status"]["is_enum"], "status IS a closed domain"


# --------------------------------------------------------------------------
# Dimension detection, exact counts
# --------------------------------------------------------------------------
class TestCompleteness:
    def test_nulls_detected_exactly(self, dirty, manifest):
        expected = manifest["dirty"]["seeded"]["completeness_null_email"]
        assert failed(dirty, "COM-NULL", "email") == expected

    def test_placeholders_detected_exactly(self, dirty, manifest):
        expected = manifest["dirty"]["seeded"]["completeness_placeholder_email"]
        assert failed(dirty, "COM-PLACEHOLDER", "email") == expected


class TestValidity:
    def test_malformed_emails_detected_exactly(self, dirty, manifest):
        expected = manifest["dirty"]["seeded"]["validity_bad_email"]
        assert failed(dirty, "VAL-EMAIL", "email") == expected

    def test_unparseable_dates_detected_exactly(self, dirty, manifest):
        expected = manifest["dirty"]["seeded"]["validity_bad_date"]
        assert failed(dirty, "VAL-DATE-PARSE", "signup_date") == expected

    def test_values_outside_the_domain_detected_exactly(self, dirty, manifest):
        expected = manifest["dirty"]["seeded"]["validity_bad_enum"]
        assert failed(dirty, "VAL-ENUM", "account_status") == expected

    def test_clean_emails_are_not_flagged(self, data_root):
        clean = _run("clean.csv", "run_clean_val")
        assert failed(clean, "VAL-EMAIL", "email") == 0


class TestUniqueness:
    def test_exact_duplicates_detected_exactly(self, dirty, manifest):
        expected = manifest["dirty"]["seeded"]["uniqueness_exact_duplicate"]
        assert failed(dirty, "UNQ-EXACT") == expected

    def test_candidate_key_duplicates_detected(self, dirty, manifest):
        expected = manifest["dirty"]["seeded"]["uniqueness_exact_duplicate"]
        assert failed(dirty, "UNQ-KEY") == expected

    def test_fuzzy_duplicates_include_the_near_misses(self, dirty, manifest):
        seeded = manifest["dirty"]["seeded"]
        # Fuzzy matching legitimately catches the exact duplicates too,
        # since an identical record is also a near-identical one.
        expected = (seeded["uniqueness_fuzzy_duplicate"]
                    + seeded["uniqueness_exact_duplicate"])
        assert failed(dirty, "UNQ-FUZZY") == expected

    def test_clean_file_has_no_duplicates(self, data_root):
        clean = _run("clean.csv", "run_clean_unq")
        assert failed(clean, "UNQ-EXACT") == 0
        assert failed(clean, "UNQ-FUZZY") == 0, "false positives are expensive"


class TestConsistency:
    def test_whitespace_detected_exactly(self, dirty, manifest):
        expected = manifest["dirty"]["seeded"]["consistency_whitespace"]
        assert failed(dirty, "CON-WHITESPACE", "last_name") == expected

    def test_casing_detected_exactly(self, dirty, manifest):
        expected = manifest["dirty"]["seeded"]["consistency_casing"]
        assert failed(dirty, "CON-CASE", "city") == expected

    def test_minority_formats_flagged_at_the_configured_coverage(self, dirty):
        """Documents the coverage trade-off rather than asserting a wish.

        The phone column holds three formats: 460 standard, 25 dotted
        international, 15 alphabetic ("555-CALL-NOW"). At the default 95%
        coverage the two largest formats together reach 97%, so only the 15
        alphabetic values fall outside the accepted set.

        This is deliberate. Lowering the bar surfaces more findings but also
        flags legitimate variation, and a false positive shown to a client is
        more expensive than a missed minor one. The threshold is per-rule
        configurable; the test below proves the knob works.
        """
        assert failed(dirty, "CON-PATTERN", "phone") == 15

    def test_lower_coverage_surfaces_minority_formats(self):
        """LOWERING coverage flags more, which is the opposite of intuition.

        `coverage` is the share of the column the accepted formats must
        explain. A high value keeps admitting formats until it is satisfied,
        so it is permissive; a low value stops sooner and flags everything
        beyond the dominant format. 0.90 stops at the first format (92%) and
        flags the other 40 rows.
        """
        import pandas as pd

        from dqa.models import ColumnProfile, DatasetProfile, Rule
        from dqa.checks.consistency import pattern_consistent
        from dqa.profiling.stats import pattern_mask

        values = (["(555) 123-4567"] * 460
                  + ["+1.555.123.4567"] * 25
                  + ["555-CALL-NOW"] * 15)
        column = ColumnProfile(name="phone", position=0, total=len(values))
        column.pattern_masks = {}
        for value in values:
            mask = pattern_mask(value)
            column.pattern_masks[mask] = column.pattern_masks.get(mask, 0) + 1

        profile = DatasetProfile(row_count=len(values), columns=[column])
        series = pd.Series(values)
        ctx = {"profile": profile}

        loose = pattern_consistent(
            series, Rule(id="r", name="r", dimension="consistency",
                         check="pattern_consistent", params={"coverage": 0.95},
                         column="phone"), ctx)
        strict = pattern_consistent(
            series, Rule(id="r", name="r", dimension="consistency",
                         check="pattern_consistent", params={"coverage": 0.90},
                         column="phone"), ctx)

        assert int((loose.failed & loose.evaluated).sum()) == 15
        assert int((strict.failed & strict.evaluated).sum()) == 40

    def test_mixed_date_formats_detected(self, dirty, manifest):
        seeded = manifest["dirty"]["seeded"]
        # The 10 unparseable values count as a differing format too.
        expected = (seeded["consistency_date_format"]
                    + seeded["validity_bad_date"])
        assert failed(dirty, "CON-DATEFMT", "signup_date") == expected


class TestAccuracy:
    def test_unknown_countries_detected_exactly(self, dirty, manifest):
        expected = manifest["dirty"]["seeded"]["accuracy_bad_country"]
        assert failed(dirty, "ACC-COUNTRY", "country") == expected

    def test_unknown_currencies_detected_exactly(self, dirty, manifest):
        expected = manifest["dirty"]["seeded"]["accuracy_bad_currency"]
        assert failed(dirty, "ACC-CURRENCY", "currency_code") == expected

    def test_typo_domains_detected_exactly(self, dirty, manifest):
        expected = manifest["dirty"]["seeded"]["accuracy_typo_domain"]
        assert failed(dirty, "ACC-EMAILDOM", "email") == expected

    def test_outliers_detected_exactly(self, dirty, manifest):
        expected = manifest["dirty"]["seeded"]["accuracy_outlier"]
        assert failed(dirty, "ACC-OUTLIER", "annual_revenue") == expected


class TestTimeliness:
    def test_future_dates_detected_exactly(self, dirty, manifest):
        expected = manifest["dirty"]["seeded"]["timeliness_future"]
        assert failed(dirty, "TIM-FUTURE", "signup_date") == expected

    def test_not_assessed_when_no_dates(self, data_root):
        """The critical case: a file with no dates must NOT score zero."""
        results = _run("no_dates.csv", "run_nodates")
        assert results["scores"]["timeliness"] is None
        assert "timeliness" in results["notAssessed"]
        assert results["notAssessed"]["timeliness"]
        # And the overall score must exclude it rather than average in a zero.
        assert results["overall"] is not None
        assert results["overall"] > 0


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
class TestScoring:
    def test_all_six_dimensions_present(self, dirty):
        from dqa import config

        assert set(dirty["scores"]) == set(config.DIMENSIONS)
        assert len(config.DIMENSIONS) == 6

    def test_integrity_is_deferred_not_emitted(self, dirty):
        from dqa import config

        assert "integrity" not in dirty["scores"], (
            "integrity must not appear until it is implemented, or the UI "
            "renders a permanently blank tile"
        )
        assert "integrity" in config.DEFERRED_DIMENSIONS

    def test_clean_file_scores_well(self, data_root):
        clean = _run("clean.csv", "run_clean_score")
        assert clean["overall"] >= 90, f"clean file scored {clean['overall']}"

    def test_dirty_scores_below_clean(self, data_root, dirty):
        clean = _run("clean.csv", "run_clean_cmp")
        assert dirty["overall"] < clean["overall"]

    def test_scores_are_bounded(self, dirty):
        for key, score in dirty["scores"].items():
            if score is not None:
                assert 0 <= score <= 100, f"{key} scored {score}"

    def test_no_rule_errors(self, dirty):
        assert dirty["ruleErrors"] == {}, f"rules errored: {dirty['ruleErrors']}"

    def test_severity_weighting_applied(self):
        """Weighting only bites when rules of different severity are mixed.

        A dimension holding one rule scores identically whatever its
        severity, because the weight cancels between numerator and
        denominator. The effect appears when a high-severity rule fails
        alongside a clean low-severity one.
        """
        from dqa.models import RuleResult
        from dqa.scoring.aggregate import dimension_score

        clean_low = RuleResult("ok", "ok", "validity", "low", "c",
                               evaluated=100, failed=0)
        failing_high = RuleResult("bad", "bad", "validity", "high", "c",
                                  evaluated=100, failed=10)
        failing_low = RuleResult("bad", "bad", "validity", "low", "c",
                                 evaluated=100, failed=10)

        weighted_high = dimension_score([clean_low, failing_high])
        weighted_low = dimension_score([clean_low, failing_low])
        assert weighted_high < weighted_low, (
            "a high-severity failure must cost more than a low-severity one"
        )


# --------------------------------------------------------------------------
# Examples and counters agreeing -- the contract's second warning
# --------------------------------------------------------------------------
class TestExamplesConsistency:
    def test_total_matches_the_rule_counter(self, dirty):
        from dqa.store import artifacts

        rule = dirty["rules"]["COM-NULL-04"]
        rows = artifacts.read_violations("run_dirty", "completeness",
                                         "COM-NULL-04", limit=10)
        assert rule["failed"] == 30
        assert len(rows) == 10, "limit must cap the sample, not the total"

    def test_examples_are_capped_not_truncated_counts(self, dirty):
        from dqa import config
        from dqa.store import artifacts

        rows = artifacts.read_violations("run_dirty", "timeliness",
                                         "TIM-STALE-01", limit=1000)
        rule = dirty["rules"].get("TIM-STALE-01")
        if rule and rule["failed"] > config.EXAMPLE_CAP:
            assert len(rows) == config.EXAMPLE_CAP
            assert rule["failed"] > len(rows), (
                "the counter must reflect the whole file, not the sample"
            )

    def test_every_violation_has_a_human_reason(self, dirty):
        from dqa.store import artifacts

        for rule_id, rule in list(dirty["rules"].items())[:20]:
            if not rule["failed"]:
                continue
            rows = artifacts.read_violations(
                "run_dirty", rule["dimension"], rule_id, limit=3
            )
            for row in rows:
                assert row["reason"], f"{rule_id} produced a violation with no reason"
                assert not row["reason"].startswith("Traceback")


# --------------------------------------------------------------------------
# CDE override
# --------------------------------------------------------------------------
class TestCdeOverride:
    def test_override_changes_the_assessed_set(self, data_root):
        chosen = ["email", "phone"]
        results = _run("dirty_known.csv", "run_override", cde_override=chosen)
        assert results["cdes"] == 2
        assert set(results["cdeColumns"]) == set(chosen)
        assert results["cdeOverridden"] is True

    def test_unknown_column_is_rejected(self, data_root):
        with pytest.raises(ValueError, match="Unknown column"):
            _run("dirty_known.csv", "run_override_bad",
                 cde_override=["no_such_column"])


# --------------------------------------------------------------------------
# Rule pack
# --------------------------------------------------------------------------
class TestRulePack:
    def test_pack_loads(self):
        from dqa.rules.loader import load_pack

        rules = load_pack()
        assert len(rules) > 20

    def test_every_rule_references_a_real_check(self):
        from dqa.rules.loader import load_pack
        from dqa.rules.registry import is_registered

        for rule in load_pack():
            assert is_registered(rule.check), f"{rule.id} -> {rule.check}"

    def test_malformed_pack_fails_loudly(self, tmp_path):
        from dqa.rules.loader import RulePackError, load_pack

        bad = tmp_path / "bad.yaml"
        bad.write_text("rules:\n  - id: X\n    name: X\n")
        with pytest.raises(RulePackError):
            load_pack(bad)

    def test_deferred_dimension_is_rejected(self, tmp_path):
        from dqa.rules.loader import RulePackError, load_pack

        bad = tmp_path / "integrity.yaml"
        bad.write_text(
            "rules:\n  - id: INT-1\n    name: Orphan\n"
            "    dimension: integrity\n    check: not_null\n"
        )
        with pytest.raises(RulePackError, match="deferred"):
            load_pack(bad)

    def test_cross_field_rules_are_tagged_for_migration(self):
        from dqa.rules.loader import load_pack

        tagged = [r for r in load_pack() if r.move_to == "integrity"]
        assert tagged, "cross-field rules must be tagged for the integrity move"
