# DQ Accelerator — API contract

This is the exact contract the React frontend (`src/`) is built against. It's
reverse-derived from the frontend code and its MSW mock (`src/mocks/`), which
you can also read directly as a working reference implementation — every
endpoint below has a matching mock handler in `src/mocks/handlers.js`.

Point the frontend at a real implementation of this contract by setting
`VITE_API_BASE_URL` in `.env.local`. No frontend code changes are needed.

> **Revision 4 — [FRONTEND PROPOSAL] Confirm CDEs before scoring.** This one
> runs the other direction: the frontend is asking the backend for a new
> capability, not adopting one the backend proposed. Today a run goes
> Ingesting → Profiling → Evaluating → Scoring → completed with no stop for a
> human to review the auto-detected CDE set until after the whole thing has
> already been scored (the existing `PUT /runs/{id}/cdes` only works on a
> `completed` run, as an optional *re*-review). The ask: pause after
> Profiling, before Evaluating, and require a human to confirm (or edit) the
> CDE set before any scoring happens at all — reviewing detected columns
> *before* they drive every score in the report, not after.
>
> **Concretely, one new status plus two small availability changes:**
>
> - A new `status: "awaiting_cdes"`, sitting between `processing` (Ingesting +
>   Profiling) and the resumed `processing` (Evaluating + Scoring). Not a flag
>   on `processing` — a genuinely distinct value, so existing polling logic
>   that already treats `status` as a closed set gets correct "don't poll,
>   don't auto-advance" behaviour with no extra checks anywhere on either side.
> - `GET /runs/{id}/profile` becomes available the moment a run reaches
>   `awaiting_cdes`, not only once `completed` — profiling is what produces
>   this data, so there's no reason to gate it on the *later* stages too.
> - `PUT /runs/{id}/cdes` becomes valid from `awaiting_cdes` as well as
>   `completed` — confirming a selection for the first time and re-reviewing
>   later are the same operation ("apply this CDE set and run Evaluating +
>   Scoring"), so no new endpoint, just a broadened status check.
>
> **Fully built on the frontend and mock as a demonstration** — see the
> shapes below, `src/mocks/db.js`'s two-phase `resolve()`, and
> `src/pages/home/AwaitingCdesView.jsx`. Nothing here is live against a real
> backend yet; this is the ask, written the same way earlier backend
> proposals were written to the frontend, so it can be reviewed and pushed
> back on the same way.
>
> **Revision 3 — `integrity` is implemented, and adopted on the frontend.**
> The dimension set is now **seven** keys, not six. `src/api/constants.js`
> has the `{ key: 'integrity', label: 'Integrity' }` entry — plain label, no
> "referential" or cross-system claim, per *Dimension set* below. The tile
> grid (`grid-cols-[repeat(auto-fill,minmax(220px,1fr))]`) wraps a 7th tile
> onto a new row with no code change; this was checked structurally against
> the CSS rule, not against a live screenshot.
>
> The drawer already rendered `rule.column` with a truthy check
> (`{rule.column && <span>...}`), so a `null` column renders as nothing
> rather than the literal string "null" — confirmed with a dedicated test
> (`src/pages/home/DimensionDrawer.test.jsx`) now that integrity makes this
> the common case (uniqueness already had one record-scoped rule; integrity
> is the first dimension where *most* rules are).
>
> `src/mocks/db.js` mirrors the documented rule ids exactly
> (`INT-CARDINALITY`, `INT-DEPENDENT-FIELD`, `INT-POSTCODE-COUNTRY-01`,
> `INT-ZIP-STATE-01`) instead of the generic `{PREFIX}-{nn}` scheme every
> other dimension uses, and gives the two whole-record rules `column: null`
> on the rule while their individual example violations still carry a real
> column — exactly the "the drawer's rule row has no column while its
> examples do" distinction this revision calls out. A filename containing
> `narrow` (or `no-integrity`) completes with integrity not-assessed, using
> this revision's own example reason text; combine it with `nodate` in one
> filename to put both dimensions in `notAssessed` at once.
>
> Not touched: `ACC-POSTCODE-COUNTRY` / `ACC-ZIP-STATE` were never hardcoded
> anywhere in `src/`, so the rename to `INT-*` needed no frontend change, as
> the doc predicted.
>
> **Revision 2 — backend additions.** Sections marked **[BACKEND PROPOSAL]**
> were added by the backend team. Everything unmarked is unchanged from
> revision 1 and is already implemented as specified. The proposals are
> additive: nothing in them breaks an existing frontend call, and the
> frontend can adopt them at whatever pace suits. Push back on any of them.
>
> **Frontend status on this revision:** the one item flagged below as
> requiring frontend work — `notAssessed` / a `null` dimension score — is
> implemented. A dimension the engine couldn't evaluate now renders as a
> distinct "Not assessed" state (dimension tile, drawer, and PDF report) and
> is excluded from the overall-score average, instead of being scored 0. See
> `src/lib/band.js` (`BAND.NOT_ASSESSED`) and the mock's `nodate` /
> `no-timeliness` filename trigger in `src/mocks/db.js` for a way to exercise
> it without a real backend.
>
> **Everything in this revision has now been adopted**, including the
> previously-optional items, with one deliberate exception noted inline in
> the upload section below (the client-side extension/size check was
> loosened to match this revision's limits, superseding FRONTEND.md's
> original FR-1.1 values — a product decision, called out explicitly rather
> than silently overridden). See the summary table at the bottom for a
> one-line status per item and exactly what changed on the frontend.

## Conventions

- Base path: `API_BASE` (default `/api`, overridable via `VITE_API_BASE_URL`).
- All request/response bodies are `application/json` unless noted.
- `status` is always exactly one of `"processing"`, `"awaiting_cdes"`,
  `"completed"`, `"failed"` — lowercase, no other values. `"awaiting_cdes"`
  is a **[FRONTEND PROPOSAL]**, revision 4 — see the note at the top of this
  document; it is not live on any real backend implementing this contract
  today.
- Dimension scores and `overall` are numbers on a **0–100** scale.
- `progress` and rule `passRate` are **0–1** fractions, not percentages.
- Dimension keys are a fixed set of seven: `completeness`, `validity`,
  `uniqueness`, `consistency`, `accuracy`, `timeliness`, `integrity`.
  (`integrity` was added in revision 3; it was six before that.)
- Any non-2xx response should include `{ "error": "..." }` (or `"detail"` —
  both are read). That string is shown to the user verbatim, so write it for
  a person, not a stack trace.

### Dimension set — `integrity` is implemented (revision 3) — adopted

The dimension set is **seven** keys. `integrity` ships in revision 3 and is
present in every completed run from now on, as an additional key in `scores`
rather than a replacement for any existing one. `timeliness` is unchanged and
stays exactly as it was.

Revision 2 said integrity "measures relationships between tables and has
nothing to assess in a single standalone CSV". That held for *cross-dataset*
integrity, which is still not implemented. It does not hold for the whole
dimension: a single file makes assertions about itself, and those are now
checked.

#### What integrity checks today, and what it does not

| Checked now, inside the uploaded file | Not checked, needs a second dataset |
| --- | --- |
| A value that determines another everywhere except on a few rows — a product code resolving to two different product names | Orphaned foreign keys pointing at a parent row that is not present |
| A field left empty on exactly the rows where its partner field is populated | A mandatory parent record carrying no child row |
| A postcode or ZIP contradicting the country or state on its own row | A lookup code resolved against a separate reference table |

**This affects the tile's copy — done.** `src/api/constants.js` labels the
tile plain `"Integrity"`, not "Referential integrity", and nothing else in
the UI implies foreign keys were checked against other systems. The rule
names inside the drawer carry the detail instead.

#### Rules you will see in the integrity drawer

| Rule id | Bound to | `column` on the rule |
| --- | --- | --- |
| `INT-CARDINALITY` | the whole record | `null` |
| `INT-DEPENDENT-FIELD` | the whole record | `null` |
| `INT-POSTCODE-COUNTRY-{nn}` | one per matching column | the column name |
| `INT-ZIP-STATE-{nn}` | one per matching column | the column name |

A `null` `column` on a record-scoped rule is not new — uniqueness already
does this — but integrity is the first dimension where the *majority* of
rules are record-scoped. **Verified:** the drawer renders a missing column as
nothing (a truthy check, `{rule.column && <span>...}</span>}`), not the
literal string "null" — see `DimensionDrawer.test.jsx`.

Individual violations from the record-scoped rules **do** carry a `column`:
the field that was contradicted or left empty. So the drawer's rule row has
no column while its examples do — the mock's `EXAMPLE_TEMPLATES.integrity`
reflects this exactly (real column names on all four rules' examples, even
the two whose rule-level `column` is `null`).

#### `integrity` is frequently `notAssessed`, by design

Integrity infers relationships from the file rather than being told them, so
a file too small or too narrow to infer from reports `null` with a reason,
exactly like `timeliness` on a file with no dates. Expect this often — more
often than for any other dimension. Reasons include: fewer than two
populated critical data elements, fewer than 20 rows to infer from, or no
applicable rule could be evaluated. All are written for a person and can be
shown verbatim.

To exercise this locally without a real backend: any uploaded filename
containing `narrow` or `no-integrity` completes with `integrity` not
assessed. The mock intentionally does **not** make this the default for
every run — a real backend will show it far more often than the mock does,
but randomizing it here would make the mock's own tests nondeterministic.

#### One renamed pair — no frontend action needed

Two rules moved out of `accuracy` into `integrity` and were renamed with it:
`ACC-POSTCODE-COUNTRY` → `INT-POSTCODE-COUNTRY`, `ACC-ZIP-STATE` →
`INT-ZIP-STATE`. Confirmed nothing in `src/` hard-coded either id, so this
was a no-op for the frontend. (It does mean a real backend's `accuracy` score
will move slightly on files containing postcodes, since two rules left that
dimension — the mock's own `accuracy` never modeled those two rules to begin
with, so there's nothing to remove there either.)

## Endpoints

### `POST /runs`

Upload a CSV and start an assessment.

- **Request:** `multipart/form-data`, one part named **`file`**.
- **Response:** `201`, returned as soon as the run is queued — don't block on
  parsing or profiling.

```json
{ "id": "run_240118", "file": "customer_master_2024.csv", "status": "processing" }
```

#### Limits and rejection responses — adopted, with the frontend loosened to match

| Limit | Value |
| --- | --- |
| Maximum upload size | 500 MB |
| Maximum rows processed in full | 5,000,000 |
| Accepted extensions | `.csv`, `.tsv`, `.txt` |
| Accepted content types | `text/csv`, `text/plain`, `application/vnd.ms-excel`, `application/octet-stream` |

Beyond 5,000,000 rows the file is **sampled**, not rejected. A deterministic
systematic sample is taken, the run completes normally, and the sampling is
disclosed in the run detail and on the PDF cover. Scores from a sampled run
are statistically representative but are not exact counts.

New failure responses on this endpoint:

- `413` — file exceeds 500 MB.
  `{ "error": "File is 812 MB. The maximum accepted size is 500 MB. Split the export into smaller files, or export a subset of columns." }`
- `415` — extension or content type not accepted.
  `{ "error": "Only CSV files are accepted. This file appears to be .xlsx — export it as CSV and try again." }`
- `400` — file is empty, has no parseable header, or has one column and no
  recognisable delimiter.

**Frontend note — deliberate change from FRONTEND.md's FR-1.1:**
`src/api/constants.js` now sets `MAX_UPLOAD_BYTES` to 500 MB and
`ACCEPTED_UPLOAD_EXTENSIONS` to `.csv`/`.tsv`/`.txt`, matching this table
exactly (superseding FR-1.1's original 200 MB / `.csv`-only values). The
`413`/`415`/`400` responses and their exact wording above are implemented
verbatim in the mock as pure, directly-tested functions —
`validateUploadFile` in `src/mocks/db.js` (see `src/mocks/db.test.js`) —
called from the `POST /runs` handler rather than duplicated inline.

More importantly, **`src/lib/validateCsv.js` is intentionally light now,
not a client-side mirror of these rules.** It only rejects two things
before the network call: no file picked, and an extension outside the
accepted set (an instant, obvious answer worth giving immediately). The
exact size ceiling, "no parseable header", row counts, and sampling are the
backend's call — the client doesn't re-implement them, it surfaces whatever
the server's `error` message says through the normal upload-failure toast.
This is a deliberate stance: **duplicate validation logic between the
frontend and backend is a liability** (the two drift, as FR-1.1 itself just
did against this revision) — the backend should be the one source of truth
for what a real upload requires.

### `GET /runs`

List all runs, newest first.

- **Polling:** every 5s from the sidebar, for as long as any run is
  `processing`. This is a hot path — keep the query cheap, no scores or counts.

```json
[
  { "id": "run_240118", "file": "customer_master_2024.csv", "status": "completed", "overall": 82.4 },
  { "id": "run_240117", "file": "orders_live.csv",          "status": "processing" },
  { "id": "run_240116", "file": "vendor_feed_raw.csv",      "status": "failed" }
]
```

`overall` is present **only** when `status` is `"completed"`.

> Backend note: this is served from a single indexed SQLite query over a
> four-column table with no joins. It stays cheap regardless of how large the
> underlying run artifacts get.

### `GET /runs/{id}`

Full detail for one run. Shape depends on `status`.

- **Polling:** every 3s, but only while this run is both selected in the UI
  and `processing`. Also a hot path.

**Processing:**

```json
{
  "id": "run_240117", "file": "orders_live.csv", "status": "processing",
  "stage": "Profiling", "stageIndex": 1, "stageCount": 4, "progress": 0.42
}
```

`stage` is shown to the user verbatim. `stageIndex` is 0-based.

> Backend note: the four stages are, in order, `Ingesting`, `Profiling`,
> `Evaluating`, `Scoring`.

**Awaiting CDE confirmation — [FRONTEND PROPOSAL], revision 4:**

```json
{
  "id": "run_240117", "file": "orders_live.csv", "status": "awaiting_cdes",
  "records": 128400, "columns": 34, "cdes": 18
}
```

Reached after `stage: "Profiling"` finishes, in place of continuing straight
to `"Evaluating"`. `cdes` here is the auto-detected *default* count, not a
final one — nothing has been scored. No `scores`/`overall` yet. The frontend
stops polling this run entirely once it sees this status (it's a resting
state, not an in-progress one) and instead shows the reviewable column list
from `GET /runs/{id}/profile` (now available at this stage — see below),
with a "confirm to start scoring" action that calls `PUT /runs/{id}/cdes`.

**Failed:**

```json
{
  "id": "run_240116", "file": "vendor_feed_raw.csv", "status": "failed",
  "error": "Could not parse row 4,812: expected 18 columns, found 21."
}
```

`error` is shown to the user directly — write it for a data analyst.

**Completed:**

```json
{
  "id": "run_240118", "file": "customer_master_2024.csv", "status": "completed",
  "overall": 82.4, "records": 128400, "cdes": 18,
  "scores": {
    "completeness": 96.2, "validity": 91.0, "uniqueness": 84.3,
    "consistency": 77.1, "accuracy": 68.4, "timeliness": 61.9,
    "integrity": 93.6
  }
}
```

`scores` must contain all seven dimension keys — a missing one renders as a
blank tile in the UI. A dimension the engine couldn't evaluate reports
**`null`**, not a missing key and not `0` — see `notAssessed` immediately
below. **Implemented.**

#### `notAssessed` — implemented

```json
{
  "id": "run_240118", "file": "customer_master_2024.csv", "status": "completed",
  "overall": 82.4, "records": 128400, "cdes": 18,
  "scores": {
    "completeness": 96.2, "validity": 91.0, "uniqueness": 84.3,
    "consistency": 77.1, "accuracy": 68.4, "timeliness": null,
    "integrity": null
  },
  "notAssessed": {
    "timeliness": "No date column was detected in this file.",
    "integrity": "Integrity compares fields against one another, and fewer than two populated critical data elements were found in this file."
  }
}
```

(Both can be `null` on the same run, as shown here — see the mock's `narrow`
+ `nodate` combined filename trigger above for how to reproduce this
locally.)

| Field | Meaning |
| --- | --- |
| `notAssessed` | Map of dimension key to a human-readable reason. A dimension appearing here carries `null` in `scores`. |

Frontend behaviour on a `null` score:

- **Dimension tile** shows the reason text in place of the score number and
  progress bar, with a neutral grey "Not assessed" pill instead of a colored
  band pill (`src/pages/home/DimensionGrid.jsx`).
- **Dimension drawer** shows the reason instead of the rules table when
  opened (`src/pages/home/DimensionDrawer.jsx`). `GET /runs/{id}/dimensions/{dim}`
  for a not-assessed dimension should return `{ key, score: null, rules: [],
  notAssessedReason }` — see that endpoint's section below.
- **`overall`** is expected to already exclude not-assessed dimensions from
  its average — the frontend does not recompute it, it displays whatever
  number the backend sends.
- **PDF report** shows the reason instead of an empty rules table on that
  dimension's page (`src/mocks/report.js` in the mock; the real report
  generator should do the same).

To exercise this locally without a real backend: any uploaded filename
containing `nodate` or `no-timeliness` (case-insensitive) completes with
`timeliness` not assessed.

#### Other additional fields on a completed run — adopted

All optional and additive, as proposed. Rendered on `ScoreHeader`
(`src/pages/home/CompletedView.jsx`):

```json
{
  "columns": 34,
  "sampled": false,
  "sampledRows": null,
  "parseWarnings": 12,
  "cdeOverridden": false
}
```

| Field | Meaning | Where it shows |
| --- | --- | --- |
| `columns` | Total columns in the file, against which `cdes` is the critical subset. | The CDEs stat reads "18 of 34" when this is present, "18" otherwise. |
| `sampled` | `true` when the file exceeded the row cap and was sampled. | A note above the dimension grid: "This assessment used a sample of X of Y rows..." |
| `sampledRows` | Rows actually assessed when `sampled` is true, else `null`. `records` remains the true row count. | Used in the sampled note above. |
| `parseWarnings` | Count of rows that could not be parsed cleanly and were excluded. | A "Parse warnings" stat, shown only when greater than 0. |
| `cdeOverridden` | `true` when the CDE set was manually changed from the detected default. | A small "Adjusted" pill next to the CDEs stat. |

To exercise `sampled`/`sampledRows` locally: any uploaded filename
containing `sampled` or `huge` completes with `sampled: true` and a
`records` count in the millions.

### `GET /runs/{id}/profile` — adopted, availability extended by revision 4

Per-column profile and the reasoning behind each CDE decision.

**[FRONTEND PROPOSAL], revision 4:** available once `status` is
`"awaiting_cdes"` **or** `"completed"` — not only after completion.
Profiling itself is what produces this data, so there's no reason to gate it
on the later Evaluating/Scoring stages too. `409` before that (still
`"processing"` through Ingesting/Profiling).

Two call sites, same endpoint: fetched when the user opens the required
review out of `awaiting_cdes` (`src/pages/home/AwaitingCdesView.jsx`), and
fetched lazily when the user clicks "View column profile" on a completed
run's summary for an optional later re-review
(`src/pages/home/ColumnProfilePanel.jsx`) — both open the same read-only-plus-
checkboxes table: column name, inferred type, fill rate, distinct ratio,
whether it was selected as a CDE, and why.

```json
{
  "columns": [
    {
      "name": "customer_id",
      "inferredType": "string",
      "semanticType": "identifier",
      "fillRate": 0.982,
      "distinctRatio": 0.998,
      "sampleValues": ["CUS-00194", "CUS-00195", "CUS-00196"],
      "isCde": true,
      "cdeScore": 0.91,
      "cdeReason": "Name matches an identifier pattern; near-unique values; well populated"
    },
    {
      "name": "created_by",
      "inferredType": "string",
      "semanticType": "free_text",
      "fillRate": 1.0,
      "distinctRatio": 0.0001,
      "sampleValues": ["ETL_LOAD", "ETL_LOAD", "ETL_LOAD"],
      "isCde": false,
      "cdeScore": 0.05,
      "cdeReason": "Matches a metadata column pattern; single repeated value"
    }
  ]
}
```

**Why this matters.** Revision 1 exposes `cdes` as a bare count with no way to
see or change which columns were chosen. CDE selection drives every score in
the report, so an incorrect selection silently produces an incorrect
assessment that we then present to a client. Detection is deterministic and
explainable, but it is still a heuristic and it will occasionally be wrong.

**Frontend note:** the panel's live "X of Y columns selected" summary is
computed client-side from the current checkbox selection, not from
`columns.length`/`isCde` alone (those only seed the initial state) — see
`PUT /runs/{id}/cdes` immediately below for how a selection is submitted.

### `PUT /runs/{id}/cdes` — adopted, scope extended by revision 4

Confirm/override the CDE set and (re-)evaluate. The source file is retained
for the run's lifetime, so this re-runs the Evaluating and Scoring stages
only; it does not re-parse or re-profile, and it typically completes in a
fraction of the original run time.

**[FRONTEND PROPOSAL], revision 4:** valid from `status: "awaiting_cdes"` as
well as `"completed"`. This is the ONE way a run leaves `awaiting_cdes` — the
required first confirmation before anything is scored — and it's also how a
`completed` run gets an optional later re-review. Same request/response
shape either way; only the starting status differs.

- **Request:**

```json
{ "columns": ["customer_id", "email", "phone", "country", "signup_date"] }
```

- **Response:** `202`, with the run returned to `processing`. The frontend
  resumes its normal 3s poll and the run transitions back to `completed`
  with new scores.

```json
{ "id": "run_240118", "status": "processing" }
```

- `400` if a named column does not exist in the file, naming the offender.
- `400` if the selection is empty.
- `409` if the run is neither `awaiting_cdes` nor `completed`.

**Frontend implementation:** two call sites, one endpoint.
`AwaitingCdesView.jsx` is the required stop: a checkbox per column, seeded
from the detected CDEs, "Confirm and start scoring" calling this endpoint —
this is the primary path every run takes once. `ColumnProfilePanel.jsx` is
the optional later path on a completed run, with a "(changed)" hint on any
row that differs from detection and a "Re-assess" button doing the same call.
Both then: immediately refetch the run's own detail (rather than waiting up
to 3s for the next scheduled poll) so the view flips to "processing" without
delay, and tell the sidebar to refresh so its status dot updates too.
`ColumnProfilePanel` additionally closes itself, since it's an overlay on top
of the completed view underneath; `AwaitingCdesView` has nothing to close —
it's the main panel already, and the refetch alone swaps it for the
processing view. A `400`/`409` response is *not* pre-empted client-side
beyond "at least one column checked" — its message surfaces through the
normal toast, per this file's general stance on where validation should live
(see the upload section above).

> Backend note: implemented exactly as specified, including the empty-file
> and unknown-column 400s. `applyCdeOverride` in `src/mocks/db.js` re-seeds
> scores rather than nudging the previous ones, so a re-assessment visibly
> produces different numbers, and a not-assessed dimension (see `notAssessed`
> above) survives an override untouched -- which columns are CDEs has no
> bearing on whether the file has a date column at all.

### `GET /runs/{id}/dimensions/{dim}`

Rule-level breakdown for one dimension. Fetched lazily, only when a dimension
tile is double-clicked — never preloaded with the rest of the run.

```json
{
  "key": "completeness",
  "score": 96.2,
  "rules": [
    { "id": "COM-01", "name": "Null rate within threshold", "passRate": 0.982 },
    { "id": "COM-02", "name": "Mandatory CDEs populated",   "passRate": 0.943 }
  ]
}
```

`score` should equal `scores.completeness` from the run detail — the drawer
and the tile sit next to each other on screen and a mismatch is very visible.

**Not-assessed shape (implemented):** a dimension appearing in the run's
`notAssessed` map returns:

```json
{ "key": "timeliness", "score": null, "rules": [], "notAssessedReason": "No date column was detected in this file." }
```

> Backend note: `score` is computed once during the Scoring stage and read
> from stored results by both this endpoint and the run detail, so the two
> are the same number by construction rather than by coincidence.

#### Optional per-rule fields — adopted

```json
{ "id": "COM-01", "name": "Null rate within threshold", "passRate": 0.982,
  "column": "customer_id", "severity": "high", "evaluated": 128400, "failed": 2311 }
```

`column` is null for rules evaluated across the whole record, such as
duplicate detection. `severity` is one of `high`, `medium`, `low` and drives
the weighting inside the dimension score, so showing it explains why two
rules with similar pass rates move the score by different amounts.

**Frontend implementation:** the drawer's rule list (`DimensionDrawer.jsx`)
shows `column` next to the rule id, and `severity` as a small neutral-toned
label next to the pass rate — deliberately not colored with the
healthy/warn/critical band palette, since severity is about how much a rule
weighs in the score, not whether it's currently passing. `evaluated` is
shown as a stat in the rule-examples overlay (next to pass rate); it's
already available on the rule object passed into that overlay, so it costs
no extra request. `failed` is not separately displayed — it's implicitly the
same number as `total` on the examples endpoint below, and showing it twice
risked looking like two different counts if they were ever computed by
different code paths (they aren't, but visually it isn't obvious they can't
disagree).

### `GET /runs/{id}/dimensions/{dim}/rules/{ruleId}/examples?limit=10`

Concrete example rows that failed one specific rule. Fetched lazily, only
when a rule row is clicked inside the dimension drawer.

```json
{
  "ruleId": "COM-01",
  "ruleName": "Null rate within threshold",
  "passRate": 0.982,
  "total": 5136,
  "examples": [
    { "row": 4812, "column": "customer_id", "value": "(null)", "reason": "Required field is empty" },
    { "row": 9201, "column": "customer_id", "value": "(null)", "reason": "Required field is empty" }
  ]
}
```

- **`limit`** caps how many examples come back; the UI requests 10 and shows
  "Showing 10 of 5,136 failing records."
- **`total`** is the real failing count across the whole file — not the
  sample size. This is the number that tells the user how big the problem
  actually is, so it must be accurate even when only 10 rows are returned.
- **Implementation note:** this should be a byproduct of the rule actually
  running over the file — capture the first N violations as the rule
  executes — not a second pass over the data triggered by this request.
- Return `404` if `ruleId` doesn't exist on that dimension. A not-assessed
  dimension has no rules, so any `ruleId` under it should also 404.

> Backend note: implemented exactly as specified. Each rule writes its first
> 200 violations to a per-rule file as it executes, and `total` is that same
> execution's counter. The drawer, the examples and the report all read one
> set of numbers, so they cannot disagree. `limit` is capped server-side at
> 200; a higher value returns 200 rather than an error.

#### [BACKEND PROPOSAL] Example capture ceiling

`examples` is capped at the first 200 violations per rule, and they are the
first 200 **in file order**, not a random sample. Two implications for
wording in the UI: "Showing 10 of 5,136" is accurate, but a hypothetical
"show all" would top out at 200. If a full failing-record export is wanted,
that would be `GET /runs/{id}/violations.csv` rather than raising this
ceiling — not requested, not built.

### `GET /runs/{id}/report?type=summary|in-depth`

Downloads a PDF report.

- `type` is exactly `summary` or `in-depth`.
- Return `409` if the run isn't `completed` yet — there's nothing to report
  on. The UI only ever shows this button on a completed run, but a direct API
  call shouldn't be able to crash report generation. **Implemented** in the
  mock (`src/mocks/handlers.js`).

```
Content-Type: application/pdf
Content-Disposition: attachment; filename="customer_master_2024-summary.pdf"
```

A not-assessed dimension's page in the PDF shows the reason instead of an
empty rules table. **Implemented** in the mock report generator
(`src/mocks/report.js`); the real report generator should do the same.

**Auth constraint:** in production the frontend requests this via a plain
`<a href download>`, not `fetch()`, so the browser issues the request on its
own and **cannot attach an `Authorization` header**. Auth for this endpoint
needs to work via cookies or a signed/pre-authorized URL. (In dev, behind the
MSW mock, the frontend falls back to `fetch()` + Blob for this one endpoint —
see `src/api/runs.js: downloadReport` — because a Service Worker doesn't
reliably intercept anchor-initiated downloads. That workaround is dev-only
and irrelevant to the real backend.)

> Backend note on auth: acknowledged, and there is no auth in this phase, so
> nothing is required of the frontend yet. When auth arrives we intend to use
> a short-lived signed URL rather than cookies — it works across origins,
> expires on its own, and avoids CSRF considerations on a download link. The
> shape would be `GET /runs/{id}/report/url?type=summary` returning
> `{ "url": "...", "expiresIn": 300 }`, with the anchor pointing at that URL.
> Flagging it now only so it isn't a surprise later; no change needed today.

### `DELETE /runs/{id}`

- Return `204` with an empty body (`200` with a JSON body also works).
- If the run is still `processing`, **cancel the underlying job** — the UI
  explicitly warns the user that deleting a processing run cancels it.
- Return `404` if the run doesn't exist.

> Backend note: cancellation is checked between chunks, so a processing run
> stops within roughly one chunk of the delete call rather than running to
> completion in the background. All artifacts including the uploaded source
> file are removed.

## Two things likely to bite

1. **`GET /runs` and `GET /runs/{id}` are polled continuously**, per open
   browser tab, for as long as anything is in flight. Avoid heavy joins on
   either — this is the path most likely to show up in a slow-query log.
2. **Rule examples must reflect the actual failing rows**, not be
   regenerated per request. If the pass rate for a rule is 96%, the 10
   examples returned should be real violations from that run, and `total`
   should be the real count — not derived independently of each other,
   or a user will notice the drawer, the report, and the examples disagreeing.

## Summary of changes for review

| Change | Type | Frontend status |
| --- | --- | --- |
| `notAssessed` and `null` dimension scores | **Behavioural** | **Done** — tile, drawer, and PDF all render "Not assessed"; excluded from the overall average |
| `GET /runs/{id}/profile` | New endpoint | **Done** — full-screen read-only column table, triggered from `ScoreHeader` |
| `PUT /runs/{id}/cdes` | New endpoint | **Done** — checkboxes in the profile panel, "Re-assess" mutation, resumes the poll |
| `columns`, `sampled`, `sampledRows`, `parseWarnings`, `cdeOverridden` on completed run | Additive fields | **Done** — all shown on `ScoreHeader` |
| `column`, `severity`, `evaluated`, `failed` per rule | Additive fields | **Done** — shown in the drawer and the rule-examples overlay |
| `413` / `415` / `400` on upload, 500 MB / `.csv`+`.tsv`+`.txt` limits | New error paths + limits | **Done, with a deliberate frontend stance change** — the client no longer duplicates these rules (see the upload section); it only checks "a file was picked" and "extension is in the accepted set," and defers everything else to the backend's response |
| `integrity` as a seventh dimension | **Behavioural, rev 3** | **Done** — `constants.js` entry, tile copy stays plain "Integrity", drawer verified to render `column: null` cleanly, mock reproduces the fixed `INT-*` rule ids and the "frequently not-assessed" behavior on demand |
| `ACC-POSTCODE-COUNTRY` / `ACC-ZIP-STATE` renamed to `INT-*` | Behavioural | **Confirmed no-op** — neither id was hard-coded anywhere in `src/` |
| 200-example capture ceiling | Documentation only | Not applicable — the frontend already just requests `limit=10` regardless of where the backend caps it |
| `awaiting_cdes` status: confirm CDEs before scoring | **[FRONTEND PROPOSAL], rev 4, behavioural** | **Built on the frontend + mock only, not live on any real backend.** Needs backend work: a new status value, `GET /runs/{id}/profile` available one stage earlier, `PUT /runs/{id}/cdes` accepting that status too. See the revision 4 note at the top of this document |
