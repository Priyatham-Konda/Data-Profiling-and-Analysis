"""Engine tests.

These assert EXACT failure counts against fixtures with a known number of
seeded defects. A test that only asserts the code ran would not have caught
any of the eight real bugs found during the first integration run.
"""
from __future__ import annotations

import csv
import json
import os
import re
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
    def test_all_seven_dimensions_present(self, dirty):
        from dqa import config

        assert set(dirty["scores"]) == set(config.DIMENSIONS)
        assert len(config.DIMENSIONS) == 7

    def test_integrity_is_emitted(self, dirty):
        from dqa import config

        assert "integrity" in dirty["scores"], (
            "integrity is implemented and must appear in every response"
        )
        assert config.DEFERRED_DIMENSIONS == []

    def test_integrity_scores_or_explains_itself(self, dirty):
        # A score or a reason, never a bare zero standing in for "could not
        # tell" -- which is the whole point of the notAssessed field.
        score = dirty["scores"]["integrity"]
        if score is None:
            assert dirty["notAssessed"].get("integrity")
        else:
            assert 0 <= score <= 100

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

    def test_deferred_dimension_is_rejected(self, tmp_path, monkeypatch):
        # Nothing is deferred now that integrity ships, but the guard has
        # to keep working for whatever gets deferred next.
        from dqa import config
        from dqa.rules.loader import RulePackError, load_pack

        monkeypatch.setattr(config, "DEFERRED_DIMENSIONS", ["lineage"])
        bad = tmp_path / "lineage.yaml"
        bad.write_text(
            "rules:\n  - id: LIN-1\n    name: Lineage\n"
            "    dimension: lineage\n    check: not_null\n"
        )
        with pytest.raises(RulePackError, match="deferred"):
            load_pack(bad)

    def test_unknown_dimension_is_rejected(self, tmp_path):
        from dqa.rules.loader import RulePackError, load_pack

        bad = tmp_path / "nonsense.yaml"
        bad.write_text(
            "rules:\n  - id: X-1\n    name: Nonsense\n"
            "    dimension: nonsense\n    check: not_null\n"
        )
        with pytest.raises(RulePackError, match="unknown dimension"):
            load_pack(bad)

    def test_cross_field_rules_moved_into_integrity(self):
        from dqa.rules.loader import load_pack

        pack = load_pack()
        integrity_ids = {r.id for r in pack if r.dimension == "integrity"}
        assert {"INT-POSTCODE-COUNTRY", "INT-ZIP-STATE"} <= integrity_ids
        assert not [r for r in pack if r.move_to], (
            "move_to is a migration marker; no rule should still carry one"
        )


def _run_file(path, run_id: str):
    """Run the engine over an arbitrary CSV rather than a named fixture."""
    from dqa.models import RunContext
    from dqa.runner import run_assessment
    from dqa.store import artifacts, registry

    artifacts.ensure(run_id)
    shutil.copy(path, artifacts.source_path(run_id))
    registry.create(run_id, path.name)
    artifacts.write_meta(run_id, {"id": run_id, "file": path.name,
                                  "status": "processing"})
    ctx = RunContext(
        run_id=run_id,
        source_path=str(artifacts.source_path(run_id)),
        original_filename=path.name,
        cancel_event=threading.Event(),
    )
    return run_assessment(ctx)


_CODE_NAMES = {
    "P-100": "Widget", "P-200": "Sprocket", "P-300": "Gasket",
    "P-400": "Flange", "P-500": "Bearing", "P-600": "Coupling",
}
_ZIP_CITIES = {
    "10001": "New York", "60601": "Chicago", "94105": "San Francisco",
    "02108": "Boston", "73301": "Austin", "98101": "Seattle",
}


def _related_csv(path, rows: int = 120, contradictions: dict | None = None):
    """A file where product_code determines product_name on every row.

    `contradictions` maps a row index to a wrong product_name, which is what
    the integrity dimension should report and nothing else should.
    """
    codes = list(_CODE_NAMES)
    zips = list(_ZIP_CITIES)
    out = []
    for i in range(rows):
        code = codes[i % len(codes)]
        zipc = zips[i % len(zips)]
        out.append({
            "customer_id": f"C{1000 + i}",
            "customer_name": f"Customer {i}",
            "product_code": code,
            "product_name": _CODE_NAMES[code],
            "postcode": zipc,
            "city": _ZIP_CITIES[zipc],
        })
    for index, wrong_name in (contradictions or {}).items():
        assert out[index]["product_name"] != wrong_name, (
            f"row {index} already holds {wrong_name}; that is no contradiction"
        )
        out[index]["product_name"] = wrong_name

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(out[0]))
        writer.writeheader()
        writer.writerows(out)
    return path


class TestIntegrity:
    """The seventh dimension, assessed within a single file.

    The first test exists because the check originally measured its
    consistency threshold across distinct KEYS rather than rows, so any file
    with fewer than twenty distinct keys discarded every pair and the
    dimension silently reported nothing at all. A check that can never fire
    passes any test that only asserts it did not crash.
    """

    def test_cardinality_reports_exactly_the_contradictions(self, data_root, tmp_path):
        path = _related_csv(
            tmp_path / "contradiction.csv",
            contradictions={7: "Gasket", 33: "Widget"},
        )
        results = _run_file(path, "run_int_contradiction")

        rule = results["rules"].get("INT-CARDINALITY")
        assert rule is not None, "INT-CARDINALITY never evaluated"
        assert rule["failed"] == 2, (
            f"expected the 2 seeded contradictions, got {rule['failed']}"
        )
        assert results["ruleErrors"] == {}

    def test_cardinality_is_silent_on_a_consistent_file(self, data_root, tmp_path):
        path = _related_csv(tmp_path / "consistent.csv")
        results = _run_file(path, "run_int_consistent")

        rule = results["rules"].get("INT-CARDINALITY")
        assert rule is not None, "INT-CARDINALITY never evaluated"
        assert rule["failed"] == 0, "a self-consistent file must produce no findings"
        assert results["scores"]["integrity"] == 100.0

    def test_contradiction_names_both_sides(self, data_root, tmp_path):
        from dqa.store import artifacts

        path = _related_csv(tmp_path / "explained.csv", contradictions={33: "Widget"})
        _run_file(path, "run_int_explained")
        examples = artifacts.read_violations(
            "run_int_explained", "integrity", "INT-CARDINALITY"
        )
        assert examples, "a contradiction must come with an example"
        reason = examples[0]["reason"]
        # The reason must name the determinant and both values, or nobody
        # reading the drawer can tell what was contradicted.
        assert "product_code" in reason
        assert "Flange" in reason and "Widget" in reason

    def test_single_column_file_is_not_assessed(self, data_root):
        results = _run("single_column.csv", "run_int_single")
        assert results["scores"]["integrity"] is None
        assert "column" in results["notAssessed"]["integrity"]

    def test_clean_file_has_no_integrity_failures(self, data_root):
        results = _run("clean.csv", "run_int_clean")
        integrity = [r for r in results["rules"].values()
                     if r["dimension"] == "integrity"]
        assert integrity, "clean.csv should still exercise integrity rules"
        assert all(r["failed"] == 0 for r in integrity), (
            "integrity must not invent findings on a clean file"
        )


class TestEmbeddedNewlines:
    """Quoted fields containing newlines, as every Salesforce export has.

    A real 69-column export failed to ingest at all: the delimiter was
    scored over physical lines, one record holding a multi-line description
    shredded into several fragments, comma collapsed to a modal field count
    of 1 and was discarded by the `fields < 2` guard, and ':' won by being
    the only candidate left. Parsing then died on the first quote.
    """

    @staticmethod
    def _multiline_csv(path, records: int = 40):
        rows = []
        for i in range(records):
            note = (
                f"Line one for {i}\n"
                "Line two: a colon, and a comma\n"
                "Line three"
            )
            rows.append({
                "customer_id": f"C{1000 + i}",
                "customer_name": f"Customer {i}",
                "city": ["Boston", "Austin", "Chicago"][i % 3],
                "description": note,
            })
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_delimiter_is_the_comma_not_the_prose(self, tmp_path):
        from dqa.ingest.reader import detect_delimiter

        path = self._multiline_csv(tmp_path / "multiline.csv")
        sample = path.read_text(encoding="utf-8")
        delimiter, confidence = detect_delimiter(sample)
        assert delimiter == ",", (
            f"detected {delimiter!r}; a colon inside a quoted note is not a delimiter"
        )
        assert confidence > 0.7

    def test_record_count_is_records_not_lines(self, data_root, tmp_path):
        path = self._multiline_csv(tmp_path / "counted.csv", records=40)
        # Three physical lines per record, so a line count would say ~120.
        assert len(path.read_text(encoding="utf-8").splitlines()) > 100

        results = _run_file(path, "run_multiline_count")
        assert results["records"] == 40, (
            f"reported {results['records']} records for a 40-record file"
        )
        assert results["ruleErrors"] == {}


class TestTimezoneAwareDates:
    """Salesforce timestamps carry an offset; hand-made files usually do not.

    Mixed, they raise "Cannot compare tz-naive and tz-aware timestamps", and
    the executor drops any rule that errors -- so the rule disappears from
    scoring rather than failing visibly.
    """

    def test_offset_dates_do_not_error_any_rule(self, data_root, tmp_path):
        path = tmp_path / "tzaware.csv"
        rows = []
        for i in range(40):
            rows.append({
                "customer_id": f"C{2000 + i}",
                "customer_name": f"Customer {i}",
                "city": ["Boston", "Austin", "Chicago"][i % 3],
                "created_date": f"2026-09-{(i % 28) + 1:02d} 17:14:44+00:00",
                "last_activity_date": f"2025-01-{(i % 28) + 1:02d} 09:00:00+00:00",
            })
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

        results = _run_file(path, "run_tz_aware")
        assert results["ruleErrors"] == {}, (
            f"timezone-aware dates errored rules: {results['ruleErrors']}"
        )

    def test_naive_dates_are_unchanged_by_the_utc_normalisation(self):
        import pandas as pd

        from dqa.checks.validity import _parse_dates

        naive = pd.Series(["2024-03-01 12:00:00", "2024-06-15 08:30:00"])
        parsed = _parse_dates(naive)
        # Parsing with utc=True and then dropping the zone must be the
        # identity for input that never carried one, or every existing
        # exact-count date assertion would shift underneath us.
        assert parsed.iloc[0] == pd.Timestamp("2024-03-01 12:00:00")
        assert parsed.iloc[1] == pd.Timestamp("2024-06-15 08:30:00")
        assert parsed.dt.tz is None


class TestReportRendering:
    """The PDF is what the client actually reads.

    The example tables once rendered as empty rows -- borders and headers,
    no values -- in every viewer. The text was present in the PDF and
    extracted correctly, so nothing downstream of the template looked wrong.
    The cause was font substitution: the tables ask for DejaVu Sans Mono,
    and where it is missing the fallback landed on a font that could not be
    embedded, so WeasyPrint emitted a Type 3 font that most viewers draw as
    nothing at all.
    """

    @staticmethod
    def _render(run_id: str):
        from dqa.report.renderer import render_report

        _run("dirty_known.csv", run_id)
        return render_report(run_id, "in-depth")

    def test_no_type3_fonts(self, data_root):
        pypdf = pytest.importorskip("pypdf")
        pdf = self._render("run_report_type3")
        reader = pypdf.PdfReader(str(pdf))

        offenders = []
        for number, page in enumerate(reader.pages, 1):
            resources = (page.get("/Resources") or {})
            try:
                resources = resources.get_object()
                fonts = (resources.get("/Font") or {}).get_object()
            except Exception:
                continue
            for obj in (fonts or {}).values():
                font = obj.get_object()
                if font.get("/Subtype") == "/Type3":
                    offenders.append((number, str(font.get("/BaseFont"))))

        assert not offenders, (
            "Type 3 fonts render as blank in most PDF viewers, so these pages "
            f"would reach a client empty: {offenders}. Install the DejaVu "
            "fonts; the container does it via fonts-dejavu-core."
        )

    def test_example_tables_carry_their_values(self, data_root):
        pypdf = pytest.importorskip("pypdf")
        pdf = self._render("run_report_values")
        reader = pypdf.PdfReader(str(pdf))
        text = chr(10).join((p.extract_text() or "") for p in reader.pages)

        assert "Example failing records" in text
        tail = text[text.index("Example failing records"):]
        # A row number followed by content is the cheapest proof the cells
        # hold values rather than being drawn as empty bordered rows.
        rows = [ln for ln in tail.splitlines() if re.match(r"^\s*\d+\s+\S", ln)]
        assert len(rows) >= 10, (
            f"only {len(rows)} populated example rows in the in-depth report"
        )

    def test_what_this_means_lists_every_dimension_weakest_first(self, data_root):
        from dqa import config
        from dqa.report.renderer import build_context

        _run("dirty_known.csv", "run_report_ranked")
        ctx = build_context("run_report_ranked", "in-depth")

        ranked = ctx["ranked"]
        assert len(ranked) == len(config.DIMENSIONS), (
            "every dimension belongs in 'What this means', not just the worst three"
        )
        scored = [d["score"] for d in ranked if d["score"] is not None]
        assert scored == sorted(scored), "scored dimensions must run weakest first"

        unscored = [d for d in ranked if d["score"] is None]
        if unscored:
            # Unscored sort last, carrying a reason rather than a number.
            assert ranked[-1]["score"] is None
            assert all(d["not_assessed"] for d in unscored)

    def test_examples_cover_every_failing_rule_the_table_lists(self, data_root):
        from dqa.report.renderer import build_context
        from dqa.store import artifacts

        run_id = "run_report_cover"
        _run("dirty_known.csv", run_id)
        ctx = build_context(run_id, "in-depth")

        for dimension in ctx["dimensions"]:
            shown = {e["rule"]["rule_id"] for e in dimension["examples"]}
            missing = []
            for rule in dimension["rules"][: ctx["rules_shown"]]:
                if not rule.get("failed") or rule["rule_id"] in shown:
                    continue
                if artifacts.read_violations(
                    run_id, dimension["key"], rule["rule_id"], limit=1
                ):
                    missing.append(rule["rule_id"])
            assert not missing, (
                f"{dimension['key']}: listed as failing but no examples below: "
                f"{missing}"
            )


class TestResumeFromProfile:
    """The second half resumes from profile.json instead of re-profiling.

    That is only safe if the stored profile round-trips exactly. These tests
    compare a resumed run against a straight-through run of the same file;
    any field lost or mangled in the JSON round trip shows up as a score
    that differs.
    """

    @staticmethod
    def _paused(fixture: str, run_id: str):
        from dqa.models import RunContext
        from dqa.runner import run_assessment
        from dqa.store import artifacts, registry

        artifacts.ensure(run_id)
        shutil.copy(FIXTURES / fixture, artifacts.source_path(run_id))
        registry.create(run_id, fixture)
        artifacts.write_meta(run_id, {"id": run_id, "file": fixture,
                                      "status": "processing"})
        ctx = RunContext(
            run_id=run_id,
            source_path=str(artifacts.source_path(run_id)),
            original_filename=fixture,
            cancel_event=threading.Event(),
        )
        return ctx, run_assessment(ctx, stop_after_profiling=True)

    @staticmethod
    def _resume(ctx, columns):
        from dqa.runner import resume_from_profile

        return resume_from_profile(ctx, columns)

    @pytest.mark.parametrize("fixture", ["dirty_known.csv", "clean.csv", "messy_format.csv"])
    def test_resumed_scores_equal_straight_through(self, data_root, fixture):
        straight = _run(fixture, f"run_straight_{fixture}")

        ctx, parked = self._paused(fixture, f"run_parked_{fixture}")
        resumed = self._resume(ctx, parked["cdeColumns"])

        assert resumed["scores"] == straight["scores"], (
            "resuming from the stored profile changed the scores, so the "
            "profile did not round-trip through JSON intact"
        )
        assert resumed["overall"] == straight["overall"]
        assert resumed["records"] == straight["records"]
        assert set(resumed["rules"]) == set(straight["rules"])
        for rule_id, rule in straight["rules"].items():
            assert resumed["rules"][rule_id]["failed"] == rule["failed"], rule_id

    def test_pausing_writes_no_results(self, data_root):
        from dqa.store import artifacts

        ctx, parked = self._paused("dirty_known.csv", "run_pause_only")
        assert artifacts.read_results("run_pause_only") is None, (
            "nothing is evaluated before confirmation, so no results may exist"
        )
        meta = artifacts.read_meta("run_pause_only")
        assert meta["status"] == "awaiting_cdes"
        assert meta["records"] == parked["records"] == 500

    def test_resume_does_not_re_profile(self, data_root, monkeypatch):
        # The point of resuming: the profiling pass must not run again.
        from dqa.profiling import stats

        ctx, parked = self._paused("dirty_known.csv", "run_no_reprofile")

        def boom(*args, **kwargs):
            raise AssertionError("resume re-ran the profiler")

        monkeypatch.setattr(stats.DatasetProfiler, "update", boom)
        self._resume(ctx, parked["cdeColumns"])

    def test_rescore_resets_an_earlier_override(self, data_root):
        # The stored profile carries the previous selection. detect() must
        # reset it, or confirming the detected set after an override would
        # still be judged against the override.
        ctx, parked = self._paused("dirty_known.csv", "run_reset_override")
        detected = parked["cdeColumns"]

        first = self._resume(ctx, detected[:-1])
        assert first["cdeOverridden"] is True

        second = self._resume(ctx, detected)
        assert second["cdeOverridden"] is False
        assert sorted(second["cdeColumns"]) == sorted(detected)

    def test_rescore_drops_examples_from_the_previous_selection(self, data_root):
        from dqa.store import artifacts

        ctx, parked = self._paused("dirty_known.csv", "run_stale_examples")
        detected = parked["cdeColumns"]
        self._resume(ctx, detected)
        before = {p.name for p in (artifacts.run_dir("run_stale_examples")
                                   / "violations").rglob("*.jsonl")}

        # Narrow to a single column; most previous rules no longer apply.
        narrowed = self._resume(ctx, detected[:1])
        after = {p.name for p in (artifacts.run_dir("run_stale_examples")
                                  / "violations").rglob("*.jsonl")}

        live = {f"{rid}.jsonl" for rid, r in narrowed["rules"].items() if r["failed"]}
        assert after <= live, (
            f"stale example files survived the re-score: {sorted(after - live)}"
        )
        assert before - after, "narrowing the selection should drop some examples"


class TestArtifactContention:
    """Windows refuses a read while os.replace holds the file.

    Unretried, the poller's read failed, GET /runs/{id} fell back to its
    defaults, and a run midway through Evaluating was reported as Ingesting,
    stage 0 -- visible to the frontend as a progress bar jumping backwards.
    """

    def test_transient_permission_error_is_retried(self, tmp_path, monkeypatch):
        from dqa.store import artifacts

        path = tmp_path / "meta.json"
        path.write_text('{"stage": "Evaluating", "stageIndex": 2}', encoding="utf-8")

        real_read = Path.read_text
        failures = {"left": 3}

        def contended(self, *args, **kwargs):
            if self == path and failures["left"]:
                failures["left"] -= 1
                raise PermissionError(13, "The process cannot access the file")
            return real_read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", contended)
        assert artifacts._read_json(path) == {"stage": "Evaluating", "stageIndex": 2}
        assert failures["left"] == 0, "the read should have been retried through"

    def test_a_missing_file_is_still_just_missing(self, tmp_path):
        from dqa.store import artifacts

        assert artifacts._read_json(tmp_path / "absent.json") is None

