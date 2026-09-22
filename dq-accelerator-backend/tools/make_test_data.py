"""Golden test fixture generator.

Generates CSV files with a KNOWN, EXACT number of seeded defects, and writes a
manifest recording them. Tests then assert exact counts rather than merely
asserting that the code ran, which is the difference between a test suite that
catches regressions and one that only catches crashes.

    python tools/make_test_data.py --out tests/fixtures
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

SEED = 20260922

FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
         "Linda", "David", "Elizabeth", "William", "Barbara", "Richard",
         "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Wilson",
        "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee"]
CITIES = [("New York", "NY", "10001"), ("Los Angeles", "CA", "90001"),
          ("Chicago", "IL", "60601"), ("Houston", "TX", "77001"),
          ("Phoenix", "AZ", "85001"), ("Philadelphia", "PA", "19101"),
          ("Seattle", "WA", "98101"), ("Denver", "CO", "80201"),
          ("Boston", "MA", "02101"), ("Atlanta", "GA", "30301")]
DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "company.com", "acme.co"]
STATUSES = ["Active", "Inactive", "Pending", "Closed"]

HEADER = [
    "customer_id", "first_name", "last_name", "email", "phone",
    "street_address", "city", "state", "zip_code", "country",
    "signup_date", "last_activity_date", "account_status",
    "annual_revenue", "employee_count", "currency_code",
    "created_by", "created_date", "source_system", "record_version",
]


def _clean_row(rng: random.Random, i: int) -> dict:
    first = rng.choice(FIRST)
    last = rng.choice(LAST)
    city, state, zipc = rng.choice(CITIES)
    # Anchored to today, not a fixed year: otherwise the staleness counts
    # drift every time the calendar moves and the tests rot.
    today = datetime.now()
    signup = today - timedelta(days=rng.randint(200, 1500))
    activity = signup + timedelta(days=rng.randint(1, 180))
    if activity > today:
        activity = today - timedelta(days=rng.randint(1, 30))
    return {
        "customer_id": f"CUS-{i:06d}",
        "first_name": first,
        "last_name": last,
        "email": f"{first.lower()}.{last.lower()}{i}@{rng.choice(DOMAINS)}",
        "phone": f"({rng.randint(200, 989)}) {rng.randint(200, 999)}-{rng.randint(1000, 9999)}",
        "street_address": f"{rng.randint(1, 9999)} {rng.choice(['Main', 'Oak', 'Elm', 'Park'])} St",
        "city": city,
        "state": state,
        "zip_code": zipc,
        "country": "US",
        "signup_date": signup.strftime("%Y-%m-%d"),
        "last_activity_date": activity.strftime("%Y-%m-%d"),
        "account_status": rng.choice(STATUSES),
        "annual_revenue": str(rng.randint(50_000, 5_000_000)),
        "employee_count": str(rng.randint(1, 5000)),
        "currency_code": "USD",
        "created_by": "ETL_LOAD",
        "created_date": "2019-01-01",
        "source_system": "ORACLE",
        "record_version": "1",
    }


def build_clean(path: Path, rows: int = 300) -> dict:
    rng = random.Random(SEED)
    data = [_clean_row(rng, i) for i in range(1, rows + 1)]
    _write(path, data)
    return {"file": path.name, "rows": rows, "seeded": {}}


RESERVED_START = 450  # rows 450+ are duplicate sources and are never mutated


def build_dirty(path: Path, rows: int = 500) -> dict:
    """Seed an exact, recorded number of defects of each kind."""
    rng = random.Random(SEED)
    data = [_clean_row(rng, i) for i in range(1, rows + 1)]
    seeded: dict[str, int] = {}

    def slots(start: int, count: int) -> list[int]:
        return list(range(start, start + count))

    # -- Completeness: 30 nulls + 20 placeholders in email ------------------
    for i in slots(0, 30):
        data[i]["email"] = ""
    seeded["completeness_null_email"] = 30
    for i in slots(30, 20):
        data[i]["email"] = "N/A"
    seeded["completeness_placeholder_email"] = 20

    # -- Validity: 25 malformed emails, 15 bad phones, 10 bad dates ---------
    for n, i in enumerate(slots(50, 25)):
        # Deliberately avoids domains that resemble a real one, so the
        # accuracy typo check has an unambiguous expected count.
        data[i]["email"] = ["not-an-email", "missing@tld", "@nodomain.invalid",
                            "spaces in@brokenaddr.invalid", "double@@at.invalid"][n % 5]
    seeded["validity_bad_email"] = 25
    for i in slots(75, 15):
        data[i]["phone"] = "555-CALL-NOW"
    seeded["validity_bad_phone"] = 15
    for i in slots(90, 10):
        data[i]["signup_date"] = "not a date"
    seeded["validity_bad_date"] = 10

    # -- Validity: 12 values outside the account_status domain --------------
    for i in slots(100, 12):
        data[i]["account_status"] = "ARCHIVED_LEGACY"
    seeded["validity_bad_enum"] = 12

    # -- Uniqueness: 20 exact duplicates, 15 fuzzy near-duplicates ----------
    # Sources are drawn from the RESERVED tail (rows 450+) which no other
    # seeding touches. Drawing from the middle meant a later mutation could
    # silently edit a source row and break the duplicate it was cloned from.
    for n, i in enumerate(slots(120, 20)):
        source = data[RESERVED_START + n]
        for key in HEADER:
            data[i][key] = source[key]
    seeded["uniqueness_exact_duplicate"] = 20

    for n, i in enumerate(slots(140, 15)):
        source = data[RESERVED_START + 25 + n]
        data[i] = dict(source)
        data[i]["customer_id"] = f"CUS-9{i:05d}"
        data[i]["first_name"] = source["first_name"][:-1] + "n"   # Joh(n)
        data[i]["email"] = source["email"].upper()
    seeded["uniqueness_fuzzy_duplicate"] = 15

    # -- Consistency: 25 odd phone formats, 20 whitespace, 18 casing --------
    for i in slots(160, 25):
        data[i]["phone"] = f"+1.{rng.randint(200,989)}.{rng.randint(200,999)}.{rng.randint(1000,9999)}"
    seeded["consistency_phone_pattern"] = 25
    for i in slots(185, 20):
        data[i]["last_name"] = f"  {data[i]['last_name']}  "
    seeded["consistency_whitespace"] = 20
    for i in slots(205, 18):
        data[i]["city"] = data[i]["city"].upper()
    seeded["consistency_casing"] = 18

    # -- Consistency: 22 rows using a second date format --------------------
    for i in slots(223, 22):
        try:
            parsed = datetime.strptime(data[i]["signup_date"], "%Y-%m-%d")
            data[i]["signup_date"] = parsed.strftime("%d/%m/%Y")
        except ValueError:
            data[i]["signup_date"] = "01/02/2020"
    seeded["consistency_date_format"] = 22

    # -- Accuracy: 14 unknown countries, 10 bad currencies, 8 typo domains --
    for i in slots(245, 14):
        data[i]["country"] = "Wakanda"
    seeded["accuracy_bad_country"] = 14
    for i in slots(259, 10):
        data[i]["currency_code"] = "ZZZ"
    seeded["accuracy_bad_currency"] = 10
    for n, i in enumerate(slots(269, 8)):
        local = data[i]["email"].split("@")[0] or "user"
        data[i]["email"] = f"{local}@{['gmial.com', 'yahooo.com', 'hotmial.com', 'outlok.com'][n % 4]}"
    seeded["accuracy_typo_domain"] = 8

    # -- Accuracy: 6 extreme revenue outliers ------------------------------
    for i in slots(277, 6):
        data[i]["annual_revenue"] = "999999999999"
    seeded["accuracy_outlier"] = 6

    # -- Timeliness: 20 stale, 9 future-dated ------------------------------
    for i in slots(283, 20):
        data[i]["last_activity_date"] = (
            datetime.now() - timedelta(days=1800)
        ).strftime("%Y-%m-%d")
    seeded["timeliness_stale"] = 20
    future = datetime.now() + timedelta(days=500)
    for i in slots(400, 9):
        data[i]["signup_date"] = future.strftime("%Y-%m-%d")
    seeded["timeliness_future"] = 9

    _write(path, data)
    return {"file": path.name, "rows": rows, "seeded": seeded}


def build_messy_format(path: Path, rows: int = 120) -> dict:
    """Semicolon delimited, cp1252 encoded, BOM, and 3 preamble rows."""
    rng = random.Random(SEED)
    data = [_clean_row(rng, i) for i in range(1, rows + 1)]
    lines = [
        "Customer Export Report",
        "Generated by Legacy CRM v3.2",
        "",
        ";".join(HEADER),
    ]
    for row in data:
        lines.append(";".join(str(row[h]).replace(";", ",") for h in HEADER))
    path.write_bytes("\ufeff".encode("utf-8") + "\n".join(lines).encode("utf-8"))
    return {"file": path.name, "rows": rows,
            "seeded": {"delimiter": ";", "preamble_rows": 3, "bom": True}}


def build_no_dates(path: Path, rows: int = 150) -> dict:
    """No date column at all: timeliness must be notAssessed, never zero."""
    rng = random.Random(SEED)
    header = ["product_id", "product_name", "category", "unit_price", "stock_qty"]
    rows_out = []
    for i in range(1, rows + 1):
        rows_out.append({
            "product_id": f"SKU-{i:05d}",
            "product_name": f"Widget {rng.choice(['A', 'B', 'C'])}{i}",
            "category": rng.choice(["Hardware", "Software", "Service"]),
            "unit_price": f"{rng.uniform(5, 500):.2f}",
            "stock_qty": str(rng.randint(0, 900)),
        })
    _write(path, rows_out, header=header)
    return {"file": path.name, "rows": rows,
            "seeded": {"expect_timeliness_not_assessed": True}}


def build_ragged(path: Path, rows: int = 100) -> dict:
    """12 rows with the wrong column count: warnings, not a failed run."""
    rng = random.Random(SEED)
    data = [_clean_row(rng, i) for i in range(1, rows + 1)]
    lines = [",".join(HEADER)]
    for n, row in enumerate(data):
        values = [str(row[h]) for h in HEADER]
        if n % 8 == 3 and n < 96:
            values.append("EXTRA_FIELD")
        lines.append(",".join(values))
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"file": path.name, "rows": rows, "seeded": {"ragged_rows": 12}}


def build_single_column(path: Path, rows: int = 50) -> dict:
    lines = ["email_address"]
    for i in range(rows):
        lines.append(f"user{i}@example.com")
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"file": path.name, "rows": rows, "seeded": {"columns": 1}}


def _write(path: Path, rows: list[dict], header: list[str] | None = None) -> None:
    header = header or HEADER
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate DQ test fixtures")
    parser.add_argument("--out", default="tests/fixtures")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    manifest = {
        "clean": build_clean(out / "clean.csv"),
        "dirty": build_dirty(out / "dirty_known.csv"),
        "messy": build_messy_format(out / "messy_format.csv"),
        "no_dates": build_no_dates(out / "no_dates.csv"),
        "ragged": build_ragged(out / "ragged.csv"),
        "single_column": build_single_column(out / "single_column.csv"),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Fixtures written to {out}")
    for key, info in manifest.items():
        print(f"  {info['file']:24s} {info['rows']:>6} rows, "
              f"{len(info['seeded'])} seeded properties")


if __name__ == "__main__":
    main()
