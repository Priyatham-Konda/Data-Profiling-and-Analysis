# Data Quality Accelerator — CSV Assessment, Phase 1

Version 1.0 · Backend specification · Internal

## 1. Objective

Accept an arbitrary CSV file exported from any source system, assess its data
quality across seven dimensions without prior knowledge of the schema, and
return scores, rule-level breakdowns, failing-record examples and a PDF
report.

"Arbitrary" is the operative constraint. The tool receives files it has never
seen, with no schema, no data dictionary and no cooperation from the source
system. Every column type, semantic meaning and business rule must be
inferred from the data itself.

This specification implements `API_CONTRACT.md` revision 2 exactly. Where the
two disagree, the contract wins.

## 2. Scope

**In scope**

- Single CSV, TSV or delimited text file uploaded over HTTP
- Automatic encoding, delimiter, header and type detection
- Automatic critical data element (CDE) detection, with manual override
- Seven dimensions: completeness, validity, uniqueness, consistency, accuracy,
  timeliness
- Rule-level breakdown and failing-record examples
- Summary and in-depth PDF reports
- Run lifecycle: create, poll, list, delete with cancellation

**Out of scope for this phase**

| Deferred | Phase |
| --- | --- |
| **Cross-dataset integrity** | Next — see section 3.1. Within-file integrity is delivered. |
| Salesforce, SFTP, Oracle connectors | Next |
| Cross-source rollup | Next |
| Data correction or cleansing | Later |
| Client-specific rule packs | Later |
| Authentication, multi-tenancy | Later |

## 3. The seven dimensions

| Dimension | Question it answers |
| --- | --- |
| Completeness | Are values present where they are required? |
| Validity | Do values conform to their expected format or domain? |
| Uniqueness | Does the same real-world entity appear more than once? |
| Consistency | Is one field formatted the same way throughout? |
| Accuracy | Are values plausible and non-contradictory? |
| Timeliness | Is the data current, and are its dates coherent? |
| Integrity | Do the relationships the file asserts about itself hold? |

### 3.1 Integrity — delivered within a file, deferred between files

Integrity splits cleanly in two, and only one half needed a second dataset.
That half was the reason the whole dimension was originally deferred.

**Delivered in this phase — relationships inside one file:**

| Check | Rule id |
| --- | --- |
| A value that determines another everywhere except on a few rows | `INT-CARDINALITY` |
| A field left empty on the rows where its partner field is populated | `INT-DEPENDENT-FIELD` |
| A postcode that cannot belong to the country on its own row | `INT-POSTCODE-COUNTRY` |
| A ZIP that cannot belong to the state on its own row | `INT-ZIP-STATE` |

The first two infer the relationship from the file rather than assuming it:
a dependency is enforced only once the file already demonstrates it across
nearly every row, and only the rows contradicting it are reported. A file
with no such structure reports `notAssessed` rather than a vacuous score.
The last two moved here from accuracy, where they had been parked under the
`move_to: integrity` marker; that marker is now gone from the rule pack.

**Still deferred — relationships between datasets:**

Orphaned foreign keys, mandatory parents with no child, and codes resolved
against a separate lookup table all need a second dataset to exist. A single
standalone CSV has no second table, so these would still score vacuously and
mislead anyone reading the report.

When multi-table sources arrive (Salesforce Account-to-Contact, Oracle tables
with declared foreign keys), this half becomes the most persuasive material
in the set, because an orphaned record is concrete and undeniable in a client
conversation.

| Requirement | Status |
| --- | --- |
| `integrity` in the dimension enum | **Implemented.** Present in `DIMENSIONS`; `DEFERRED_DIMENSIONS` is now empty |
| A dimension that can be marked unassessable | Implemented via `notAssessed`, used by timeliness and integrity |
| Stateful checks that see the whole dataset | Implemented; used by uniqueness and both new integrity checks |
| Within-file integrity rules | **Implemented** in `dqa/checks/integrity.py` |
| Cross-dataset integrity rules | **Not implemented.** Commented at the foot of `rules/default_pack.yaml` |
| Multi-dataset run context | **Not implemented.** The run context holds one dataset. This remains the one structural change the cross-dataset half requires. |

## 4. Ingestion

The first stage has to survive whatever the source system produced.

| Problem | Handling | Library |
| --- | --- | --- |
| Unknown encoding | Detect; prefer UTF-8 with BOM stripped; fall back through cp1252, latin-1 | `charset-normalizer` |
| Unknown delimiter | Score `,` `;` `\t` `\|` `:` by column-count stability across a 64 KB sample; highest stable count wins | stdlib `csv` plus own scoring |
| Preamble junk rows | Scan the first 20 rows; the header is the first row whose field count equals the modal count, whose cells are non-numeric, non-empty and unique | own |
| Quoted embedded newlines | Standard CSV quoting, handled by the parser | `pandas` |
| Ragged rows | Recorded as parse warnings and excluded; never fatal unless every row fails | `pandas` `on_bad_lines` |
| Duplicate column names | Suffixed `_1`, `_2`, recorded as a consistency finding | own |
| Excel artifacts | `=` prefixed formula cells, `#REF!`, `#N/A`, and thousands separators in numerics are normalised before typing | own |
| Files over the row cap | Deterministic systematic sample, disclosed in the response and on the report cover | own |

### 4.1 Type inference

Per column, on a sample of up to 10,000 non-null values, tested in order and
requiring 95% agreement: integer, decimal, boolean, date, datetime, string.

### 4.2 Semantic type inference

Semantic type is what makes rules possible without a schema. Two signals
combine: the column name against a pattern list, and the values against a
validator, requiring an 80% match rate.

Recognised: `identifier`, `email`, `phone`, `person_name`, `org_name`,
`address`, `postcode`, `country`, `state`, `city`, `currency_code`, `amount`,
`date`, `datetime`, `url`, `boolean_flag`, `category`, `free_text`.

Name signal and value signal disagreeing is itself recorded, because a column
called `email` holding 40% non-emails is a finding in its own right.

## 5. CDE detection

Scored per column, range 0 to 1, threshold 0.5, with every contribution
recorded so the decision is explainable through `GET /runs/{id}/profile`.

| Signal | Weight |
| --- | --- |
| Name matches a business-entity pattern | +0.35 |
| Name matches a metadata pattern (`created*`, `modified*`, `etl_*`, `_ts`, `source_system`, `isdeleted`, `ownerid`, `batch_id`) | −0.60 |
| Semantic type is a recognised business type, not `free_text` | +0.20 |
| Fill rate between 0.2 and 1.0 | +0.15 |
| Fill rate 0 | Force to 0, nothing to assess |
| Distinct ratio ≥ 0.9 on a string column | +0.15 |
| Distinct count of 1 across the whole file | −0.50 |
| Distinct ratio < 0.001 on a column over 1,000 rows | −0.25 |

The threshold is tuned so roughly 30–40% of columns are selected, which
matches established practice. Every score and reason is persisted.

Manual override via `PUT /runs/{id}/cdes` re-runs evaluation and scoring
against the retained source file, without re-parsing or re-profiling.

## 6. Algorithms per dimension

### Completeness
Null, empty string, whitespace-only, and a placeholder list (`n/a`, `na`,
`null`, `none`, `nil`, `unknown`, `tbd`, `xxx`, `-`, `.`, `?`, `test`,
`no value`). Evaluated per CDE column.

### Validity
- Regex conformance by semantic type
- Real phone validation via `phonenumbers`, not a regex
- Date parseability, plus a plausibility window of 1900 to today + 10 years
- Numeric range and sign sanity (negative quantities, negative ages)
- Value length overflow against the column's observed 99th percentile
- Inferred enum domains: where distinct count < 25 and the top values cover
  ≥ 95% of rows, the remainder are domain violations

### Uniqueness
Three layers, cheapest first.

1. Exact duplicate on the full record hash
2. Exact duplicate on an inferred candidate key (CDE columns with distinct
   ratio ≥ 0.95)
3. Fuzzy near-duplicates

Fuzzy matching uses **blocking** to remain tractable. Blocking keys are the
normalised email local part, the last 7 digits of a normalised phone, and the
first 3 characters of a name field combined with its metaphone code. Only
records sharing a block are compared, using RapidFuzz `token_set_ratio` with
a threshold of 88, and clusters are built with union-find.

Blocking is what makes this linear rather than quadratic. Without it, 128,000
records is 8 billion comparisons; with it, a few million.

### Consistency
- **Pattern signature:** each value is masked (letter runs → `A`, digit runs
  → `9`, punctuation preserved), the masks are histogrammed, and values whose
  mask falls outside the set covering 95% of the column are flagged. This is
  the single most effective consistency check and needs no configuration.
- Casing mixture within one column
- Leading or trailing whitespace
- Multiple date formats within one column
- Conflicting values for the same entity key across rows

### Accuracy
- Reference conformance: ISO country, currency and US state lists bundled
  directly, not via `pycountry`, which is LGPL
- Cross-field contradiction: postcode format versus country, US ZIP prefix
  versus state, email domain against a common-typo list
- Impossible values: future dates of birth, negative amounts where the
  column is otherwise non-negative, ages over 130
- Statistical outliers via **median absolute deviation** with a modified
  z-score threshold of 3.5, chosen over standard deviation because business
  data is routinely skewed and the mean is not robust

### Timeliness
- Staleness distribution against the most recent date column per record
- Share of records not updated within a configurable window, default 730 days
- Future-dated records
- Date range coverage gaps

Degrades to `notAssessed` with an explicit reason when no date column exists.

## 7. Architecture

```
POST /runs ──▶ queue ──▶ ThreadPoolExecutor
                              │
    ┌─────────────────────────┴─────────────────────────┐
    │ Stage 1 Ingesting  detect encoding/delimiter/header│
    │ Stage 2 Profiling  chunk stream → column stats     │
    │ Stage 3 Evaluating chunk stream → rule execution   │
    │ Stage 4 Scoring    aggregate → results.json        │
    └───────────────────────────────────────────────────┘
```

Two passes over the file, both streaming in 50,000-row chunks. Cancellation
is checked between chunks.

Rule execution captures counters and the first 200 violations **in the same
pass**, which is what guarantees `total`, `passRate` and `examples` agree.

### 7.1 Check interface

Two kinds, both registered into one registry:

- **Cell checks** receive a column Series and return an evaluated mask and a
  failed mask. Most rules are of this kind.
- **Stateful checks** accumulate across chunks and finalise at the end.
  Uniqueness is the only current user; integrity will be the second.

Adding a check means one decorated function and one YAML entry. The executor
never changes.

### 7.2 Artifacts

```
data/runs/{run_id}/
  source.csv
  meta.json          run metadata, stage, progress
  profile.json       per-column profile and CDE reasoning
  results.json       scores, per-rule counters
  violations/{dimension}/{rule_id}.jsonl
  report-summary.pdf
  report-in-depth.pdf
data/runs.db         SQLite: id, file, status, overall, created_at
```

`GET /runs` reads only the SQLite table, which keeps the 5-second poll on a
single indexed query with no joins.

## 8. Scoring

Per rule, pass rate is `1 − failed / evaluated`. Per dimension, the score is
a severity-weighted pass rate with weights high 3, medium 2, low 1:

```
score_d = 100 × (1 − Σ(w_r × failed_r) / Σ(w_r × evaluated_r))
```

Overall is the mean of assessed dimension scores. Unassessed dimensions are
excluded from the mean rather than counted as zero.

## 9. Dependencies and licences

| Package | Licence |
| --- | --- |
| fastapi, pydantic, python-multipart | MIT |
| uvicorn | BSD 3-Clause |
| pandas, numpy | BSD 3-Clause |
| pyarrow | Apache 2.0 |
| charset-normalizer | MIT |
| python-dateutil | Apache 2.0 / BSD |
| rapidfuzz | MIT |
| phonenumbers | Apache 2.0 |
| metaphone | BSD |
| pyyaml, jinja2 | MIT / BSD |
| weasyprint | BSD 3-Clause |
| pytest, httpx | MIT / BSD |

All permissive. No copyleft, no commercial licence, no purchase required.
Paramiko, the one LGPL item in the wider stack, is not needed in this phase
because there is no SFTP connector.

`pycountry` is deliberately excluded (LGPL 2.1); reference data is bundled
directly instead.

## 10. Testing

Golden fixtures with a known, exact defect count per dimension, generated by
`tools/make_test_data.py`. Tests assert exact failure counts, not that the
code merely runs.

| Fixture | Purpose |
| --- | --- |
| `clean.csv` | All dimensions should score at or near 100 |
| `dirty_known.csv` | Exact seeded defect counts per dimension |
| `messy_format.csv` | Semicolon delimiter, cp1252, BOM, 3 preamble rows |
| `no_dates.csv` | Timeliness must return `notAssessed`, not 0 |
| `ragged.csv` | Parse warnings without run failure |
| `single_column.csv` | Degenerate input handled gracefully |

## 11. Open items

1. Fuzzy match threshold of 88 is a starting point and should be tuned
   against a real client export before the first demo.
2. The staleness window default of 730 days is arbitrary and should be set
   per engagement.
3. Integrity requires a multi-dataset run context, the one structural change
   in the next phase.
4. Report branding assets are not yet available.
