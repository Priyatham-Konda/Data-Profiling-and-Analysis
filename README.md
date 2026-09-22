# Data Quality Accelerator — Backend

Assesses an arbitrary CSV export across six data quality dimensions and
returns scores, rule-level breakdowns, failing-record examples and a PDF
report. Implements `API_CONTRACT.md` revision 2.

The point is that it receives files it has never seen, with no schema and no
cooperation from the source system. Column types, semantic meaning and
applicable rules are all inferred from the data.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

python tools/make_test_data.py --out tests/fixtures   # golden fixtures
pytest                                                 # 73 tests
uvicorn dqa.api.app:app --reload --port 8000
```

Or with Docker, which also brings the PDF rendering libraries:

```bash
docker compose up --build
```

Point the frontend at it by setting `VITE_API_BASE_URL=http://localhost:8000/api`
in its `.env.local`. No frontend code changes are needed.

Try it:

```bash
curl -F "file=@tests/fixtures/dirty_known.csv" http://localhost:8000/api/runs
curl http://localhost:8000/api/runs/<run_id>
curl "http://localhost:8000/api/runs/<run_id>/report?type=in-depth" -o report.pdf
```

## The six dimensions

| Dimension | Question | Main techniques |
| --- | --- | --- |
| Completeness | Are values present where required? | Nulls, blanks, placeholder vocabulary |
| Validity | Does the value fit its format or domain? | Semantic-type regex, libphonenumber, date parsing, inferred enum domains |
| Uniqueness | Does one entity appear twice? | Exact hash, inferred candidate key, blocked fuzzy matching |
| Consistency | Is the field formatted the same throughout? | Pattern-signature histogram, casing, whitespace, mixed date formats |
| Accuracy | Are values plausible and non-contradictory? | Reference lists, cross-field contradiction, MAD outliers |
| Timeliness | Is the data current? | Staleness window, future dates, range coverage |

### Integrity is deferred, not dropped

**Integrity is the seventh dimension and must be implemented in the phase
after this one.** It measures relationships *between* datasets — orphaned
foreign keys, unresolved lookup codes, missing mandatory parents. A single
standalone CSV has no second dataset to relate to, so scoring it here would
be vacuous and would mislead anyone reading the report.

The full implementation checklist is in `dqa/config.py` under
`DEFERRED_DIMENSIONS`. In summary: add the key to `DIMENSIONS`, extend
`RunContext` to hold multiple datasets (the only structural change needed),
implement `dqa/checks/integrity.py` against the existing stateful-check
interface, uncomment the integrity section of `rules/default_pack.yaml`, and
move the two rules tagged `move_to: integrity` out of accuracy.

A test asserts integrity does **not** appear in any response until that work
is done, because emitting an unimplemented key would render a permanently
blank tile in the UI.

## Architecture

```
POST /runs ─▶ queue ─▶ ThreadPoolExecutor
                   │
   Stage 1 Ingesting   encoding, delimiter, header detection
   Stage 2 Profiling   chunk stream ─▶ column statistics ─▶ CDE detection
   Stage 3 Evaluating  chunk stream ─▶ rule execution
   Stage 4 Scoring     aggregate ─▶ results.json
```

Two streaming passes, 50,000 rows per chunk. Cancellation is checked between
chunks, so deleting a processing run actually stops it.

```
dqa/
  config.py          all tunable thresholds, and the integrity checklist
  models.py          shared dataclasses
  ingest/reader.py   encoding, delimiter, header, chunked streaming
  profiling/         column statistics, type and semantic inference
  cde/detector.py    critical data element scoring, explainable
  rules/             rule models, YAML loader, check registry, executor
  checks/            one module per dimension
  scoring/           severity-weighted aggregation
  store/             SQLite registry, run artifacts
  report/            Jinja2 + WeasyPrint
  api/               FastAPI routes
rules/default_pack.yaml
```

### Two design decisions worth knowing

**Rules are configuration, not code.** A rule is a YAML entry naming a
registered check, its parameters and which columns it binds to. This is what
makes the later phases additive: client-specific rule packs (phase 2) and
agent-generated packs (phase 3) need no engine change. Adding a check means
one decorated function and one YAML entry; the executor never changes.

**Counters and examples come from one pass.** Each rule accumulates its
evaluated and failed counts *and* captures its first 200 violations as it
executes, written to `violations/{dimension}/{rule_id}.jsonl`. The dimension
drawer, the examples endpoint and the PDF all read the same numbers, so they
cannot disagree — which is what the API contract's second warning asks for.

### Run artifacts

```
data/runs/{run_id}/
    source.csv          retained so CDE overrides can re-assess
    meta.json           status, stage, progress
    profile.json        per-column profile and CDE reasoning
    results.json        scores and per-rule counters
    violations/{dimension}/{rule_id}.jsonl
    report-{type}.pdf
data/runs.db            SQLite: id, file, status, overall
```

`GET /runs` is polled every 5 seconds per open tab, so it reads only the
SQLite table — a single indexed query, no joins, no scores.

Deleting a run removes the whole directory including the uploaded file, which
is what makes "run it, then destroy it" a single operation rather than a hunt
for stray files.

## Configuration

See `.env.example`. The thresholds most likely to need tuning against a real
client export are in `dqa/config.py`:

| Setting | Default | Note |
| --- | --- | --- |
| `CDE_THRESHOLD` | 0.5 | Selects roughly 30–40% of columns |
| `FUZZY_THRESHOLD` | 88 | RapidFuzz `token_set_ratio` for near-duplicates |
| `PATTERN_COVERAGE` | 0.95 | **Higher is more permissive** — see below |
| `MAD_Z_THRESHOLD` | 3.5 | Modified z-score for outliers |
| `STALENESS_DAYS` | 730 | Should be set per engagement |
| `MAX_ROWS_FULL` | 5,000,000 | Beyond this the file is sampled and disclosed |

**The pattern-coverage knob runs backwards from intuition.** It is the share
of the column the accepted formats must explain, so a high value keeps
admitting formats until the quota is met. At the 0.95 default, a format used
by 5% of rows sits inside the accepted set and is not flagged. Lower it to
surface minority formats, at the cost of flagging legitimate variation.
`tests/test_engine.py::TestConsistency` pins both directions.

## Testing

```bash
python tools/make_test_data.py --out tests/fixtures
pytest
```

Fixtures carry a **known, exact** number of seeded defects recorded in
`manifest.json`, and the tests assert exact counts rather than merely that
the code ran. That distinction caught eight real bugs during the first
integration run, including value normalisation stripping the whitespace that
the whitespace check exists to detect, and date columns scoring below the CDE
threshold so timeliness was never assessable.

| Fixture | Purpose |
| --- | --- |
| `clean.csv` | Scores must be high; no false positives |
| `dirty_known.csv` | 18 seeded defect types with exact counts |
| `messy_format.csv` | Semicolon delimiter, BOM, 3 preamble rows |
| `no_dates.csv` | Timeliness must be `notAssessed`, never 0 |
| `ragged.csv` | Parse warnings without a failed run |
| `single_column.csv` | Degenerate input |

## Licences

Every dependency is permissively licensed. No copyleft, no commercial
licence, nothing for a client to purchase. Verified against each project's
published metadata.

MIT: fastapi, pydantic, python-multipart, PyYAML, rapidfuzz, pytest,
charset-normalizer, SQLAlchemy.
BSD 3-Clause: pandas, numpy, uvicorn, Jinja2, WeasyPrint, Metaphone, httpx.
Apache 2.0: pyarrow, phonenumbers, python-dateutil.

Deliberately excluded and recorded in `requirements.txt`:

- **pycountry** (LGPL 2.1) — reference data is bundled in
  `dqa/checks/reference.py` instead.
- **paramiko** (LGPL 2.1) — not needed until the SFTP connector phase, at
  which point it needs a decision. See `API_CONTRACT.md`.

WeasyPrint depends on Pango and Cairo, which are LGPL but are consumed as
unmodified OS packages inside the container, the same arrangement as the
Linux distribution itself. No dependency transmits any obligation to our
source code.

## Known limitations

1. **Accuracy cannot be fully assessed without an external source of truth.**
   Knowing a product weight is wrong requires knowing the right weight. What
   is done here is reference-list conformance, cross-field contradiction and
   statistical outliers. The report states this rather than overclaiming.
2. **Scores dilute on wide files.** Failures spread across many clean columns
   produce a high dimension score even when specific columns are poor. The
   rule-level numbers are stark; the rollup softens them. Worth deciding
   whether the report should lead with failing-record counts.
3. **The fuzzy threshold of 88 is a starting point** and should be tuned
   against a real client export before the first demo.
4. **No authentication.** Deliberate for this phase. The report download
   cannot carry an `Authorization` header, so a signed URL is the intended
   approach when auth arrives.

## Next phase

Salesforce, SFTP and Oracle connectors behind the existing `read()` /
`describe()` interface; cross-source rollup; and the integrity dimension.
Salesforce is the highest-value addition, because its Describe API supplies
declared types, required flags and picklist values, which means validity and
consistency rules can be generated from real metadata rather than inferred.
