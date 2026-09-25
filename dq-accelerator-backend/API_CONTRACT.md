# DQ Accelerator — API contract

This is the exact contract the React frontend (`src/`) is built against. It's
reverse-derived from the frontend code and its MSW mock (`src/mocks/`), which
you can also read directly as a working reference implementation — every
endpoint below has a matching mock handler in `src/mocks/handlers.js`.

Point the frontend at a real implementation of this contract by setting
`VITE_API_BASE_URL` in `.env.local`. No frontend code changes are needed.

> **Revision 4 — critical data elements are confirmed before scoring.**
> A run no longer goes straight from upload to scores. It pauses after
> Profiling in a new status, **`awaiting_cdes`**, and nothing is scored
> until a person confirms the detected columns with `PUT /runs/{id}/cdes`.
> No new endpoints; four existing ones accept one more situation. See
> *Run lifecycle* below. `BACKEND_CHANGES.md` is the plain-language brief
> this revision implements.
>
> **Revision 3 — `integrity` is implemented.** The dimension set is now
> **seven** keys, not six. `integrity` appears in `scores` on every completed
> run from this revision onward. This is the one change in revision 3 that
> needs frontend work, and it is small: one entry in `src/api/constants.js`
> and a tile grid that tolerates seven tiles. Read *Dimension set* below
> before you add the tile — what integrity does and does not check affects
> the label you put on it.
>
> **Revision 2 — backend additions.** Sections marked **[BACKEND PROPOSAL]**
> were added by the backend team. Everything unmarked is unchanged from
> revision 1 and is already implemented as specified. The proposals are
> additive: nothing in them breaks an existing frontend call, and the
> frontend can adopt them at whatever pace suits. Push back on any of them.

## Conventions

- Base path: `API_BASE` (default `/api`, overridable via `VITE_API_BASE_URL`).
- All request/response bodies are `application/json` unless noted.
- `status` is always exactly one of `"processing"`, `"awaiting_cdes"`,
  `"completed"`, `"failed"` — lowercase, no other values. (`awaiting_cdes`
  was added in revision 4; there were three before that.)
- Dimension scores and `overall` are numbers on a **0–100** scale.
- `progress` and rule `passRate` are **0–1** fractions, not percentages.
- Dimension keys are a fixed set of seven: `completeness`, `validity`,
  `uniqueness`, `consistency`, `accuracy`, `timeliness`, `integrity`.
  (`integrity` was added in revision 3; it was six before that.)
- Any non-2xx response should include `{ "error": "..." }` (or `"detail"` —
  both are read). That string is shown to the user verbatim, so write it for
  a person, not a stack trace.

### Dimension set — `integrity` is implemented (revision 3)

The dimension set is **seven** keys. `integrity` ships in revision 3 and is
present in every completed run from now on, as an additional key in `scores`
rather than a replacement for any existing one. `timeliness` is unchanged and
stays exactly as it was.

Revision 2 said integrity "measures relationships between tables and has
nothing to assess in a single standalone CSV". That holds for *cross-dataset*
integrity, which is still not implemented. It does not hold for the whole
dimension: a single file makes assertions about itself, and those are now
checked.

#### What integrity checks today, and what it does not

| Checked now, inside the uploaded file | Not checked, needs a second dataset |
| --- | --- |
| A value that determines another everywhere except on a few rows — a product code resolving to two different product names | Orphaned foreign keys pointing at a parent row that is not present |
| A field left empty on exactly the rows where its partner field is populated | A mandatory parent record carrying no child row |
| A postcode or ZIP contradicting the country or state on its own row | A lookup code resolved against a separate reference table |

**This affects your UI copy.** Do not label the tile "referential integrity"
and do not imply foreign keys were validated against other systems — none
were. The PDF report states the distinction in writing, and a tile that
contradicts the report in front of a client is worse than no tile. Plain
"Integrity" is fine; the rule names in the drawer carry the detail.

#### Rules you will see in the integrity drawer

| Rule id | Bound to | `column` on the rule |
| --- | --- | --- |
| `INT-CARDINALITY` | the whole record | `null` |
| `INT-DEPENDENT-FIELD` | the whole record | `null` |
| `INT-POSTCODE-COUNTRY-{nn}` | one per matching column | the column name |
| `INT-ZIP-STATE-{nn}` | one per matching column | the column name |

A `null` `column` on a record-scoped rule is not new — the uniqueness
dimension already does this — but integrity is the first dimension where the
*majority* of rules are record-scoped, so it is worth re-checking that the
drawer renders a missing column cleanly rather than printing "null".

Individual violations from the record-scoped rules **do** carry a `column`:
the field that was contradicted or left empty. So the drawer's rule row has
no column while its examples do. That is intentional, not an inconsistency.

#### `integrity` is frequently `notAssessed`, by design

Integrity infers relationships from the file rather than being told them, so
a file too small or too narrow to infer from reports `null` with a reason,
exactly like `timeliness` on a file with no dates. Expect to render this
often — more often than for any other dimension. Reasons you may receive:

```json
{
  "notAssessed": {
    "integrity": "Integrity describes how fields relate to one another, and this file has only one column."
  }
}
```

The other reasons follow the same shape: fewer than two populated critical
data elements, fewer than 20 rows to infer from, or no applicable rule could
be evaluated. All are written for a person and can be shown verbatim.

#### One renamed pair

Two rules moved out of `accuracy` into `integrity` and were renamed with it:

| Was | Now |
| --- | --- |
| `ACC-POSTCODE-COUNTRY` | `INT-POSTCODE-COUNTRY` |
| `ACC-ZIP-STATE` | `INT-ZIP-STATE` |

They are cross-field checks — the finding is a contradiction between two
columns of one row — so they belong to integrity rather than accuracy. If
anything in the frontend keys off those rule ids, it needs updating; nothing
in the current `src/` does, so this should be a no-op for you. The accuracy
score will move slightly on files containing postcodes, because two rules
left that dimension.

## Run lifecycle (revision 4)

```
upload ─▶ processing ─▶ awaiting_cdes ──PUT /cdes──▶ processing ─▶ completed
          Ingesting,       (parked,                  Evaluating,
          Profiling        waiting on a person)      Scoring
```

Any `processing` step can end in `failed` instead. A completed run can be
sent back through `PUT /cdes` to re-score it against a different selection,
exactly as in revision 2.

**Stop polling at `awaiting_cdes`.** It is a resting state, not work in
progress: nothing changes until the user acts, so a 3-second poll there
only burns requests. Resume polling after `PUT /cdes` returns.

**Progress only moves forward.** After confirmation the run reports
`Evaluating` (stage 2) then `Scoring` (stage 3). It does not revisit
`Ingesting` or `Profiling`: the second half resumes from the stored profile
rather than reading the file from the top. A progress bar can therefore
treat the two halves as one continuous 0–3 sequence split by the pause.

**There is always at least one detected CDE** for a file with any populated
column. Detection falls back to the strongest third of columns when none
clear the threshold, so the confirm screen is never empty — though the user
may still deselect down to one.

### The four run states side by side

`GET /runs/{id}` returns a different shape per status:

| Status | Fields |
| --- | --- |
| `processing` | `id`, `file`, `status`, `stage`, `stageIndex`, `stageCount`, `progress` |
| `awaiting_cdes` | `id`, `file`, `status`, `records`, `columns`, `cdes` |
| `completed` | `id`, `file`, `status`, `overall`, `records`, `cdes`, `scores`, `columns`, `sampled`, `sampledRows`, `parseWarnings`, `cdeOverridden`, `notAssessed` |
| `failed` | `id`, `file`, `status`, `error` |

`awaiting_cdes` is the only shape with neither `scores` nor `overall`,
because nothing has been evaluated. The three counts it does carry all come
from Profiling, which is what lets the state exist before Evaluating runs.

## Endpoints

### `POST /runs`

Upload a CSV and start an assessment.

- **Request:** `multipart/form-data`, one part named **`file`**.
- **Response:** `201`, returned as soon as the run is queued — don't block on
  parsing or profiling.

```json
{ "id": "run_240118", "file": "customer_master_2024.csv", "status": "processing" }
```

#### [BACKEND PROPOSAL] Limits and rejection responses

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

The UI should surface these as ordinary error toasts. All three are written
for a human already.

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

**Awaiting CDEs** (revision 4):

```json
{
  "id": "run_240118", "file": "customer_master_2024.csv",
  "status": "awaiting_cdes",
  "records": 128400, "columns": 34, "cdes": 18
}
```

`columns` is the total column count; `cdes` is how many were auto-detected.
Fetch `GET /runs/{id}/profile` for the per-column list and the reason each
was or was not selected, which is what the confirm screen shows.

> Backend note: the four stages are, in order, `Ingesting`, `Profiling`,
> `Evaluating`, `Scoring`.

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
blank tile in the UI. A key whose value is `null` is a *scored-but-not-
assessable* dimension and is a different case; see `notAssessed` below.

#### [BACKEND PROPOSAL] Additional fields on a completed run

All optional and additive. A frontend that ignores them behaves exactly as
today.

```json
{
  "id": "run_240118", "file": "customer_master_2024.csv", "status": "completed",
  "overall": 82.4, "records": 128400, "cdes": 18,
  "scores": { "...": "as above" },

  "columns": 34,
  "sampled": false,
  "sampledRows": null,
  "notAssessed": {
    "timeliness": "No date column was detected in this file.",
    "integrity": "Integrity compares fields against one another, and fewer than two populated critical data elements were found in this file."
  },
  "parseWarnings": 12,
  "cdeOverridden": false
}
```

| Field | Meaning |
| --- | --- |
| `columns` | Total columns in the file, against which `cdes` is the critical subset. "18 of 34" reads far better on the summary than "18". |
| `sampled` | `true` when the file exceeded the row cap and was sampled. |
| `sampledRows` | Rows actually assessed when `sampled` is true, else `null`. `records` remains the true row count of the file. |
| `notAssessed` | Map of dimension key to a human-readable reason. A dimension appearing here still appears in `scores` with a value of `null`. Suggested rendering: a greyed tile showing the reason rather than a zero. |
| `parseWarnings` | Count of rows that could not be parsed cleanly and were excluded. Worth surfacing, because a client noticing it later is worse than us stating it. |
| `cdeOverridden` | `true` only when the confirmed CDE set differs from the detected default. Confirming the detected set as-is leaves it `false` (revision 4; see `PUT /runs/{id}/cdes`). |

**Please note the `notAssessed` interaction.** A dimension that cannot be
assessed will carry `null` in `scores`, not `0`. Scoring a file `0` for
timeliness because it has no dates would be actively misleading in front of
a client. If rendering `null` is awkward, tell us and we will send the key
omitted instead — but please don't display it as zero.

### [BACKEND PROPOSAL] `GET /runs/{id}/profile`

Per-column profile and the reasoning behind each CDE decision. Not on any
hot path — fetched only if the user opens a "columns" view.

**Available from `awaiting_cdes` onward** (revision 4), not only once a run
completes — the profile comes from Profiling, so it is complete before
anything is scored. This is the data the confirm screen is built from.
Returns `409` for a run still in its first `processing` phase, before
Profiling has finished.

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

### [BACKEND PROPOSAL] `PUT /runs/{id}/cdes`

Confirm or change the CDE set, then evaluate and score. Two starting
states, one behaviour:

- **From `awaiting_cdes`** (revision 4): this is what starts Evaluating and
  Scoring for the first time. Every run passes through here once.
- **From `completed`**: re-scores a finished run against a different
  selection, as in revision 2.

Either way it runs Evaluating and Scoring only. It does not re-parse or
re-profile — the source file is retained for the run's lifetime and scoring
resumes from the stored profile — so it typically finishes in a fraction of
the original run time.

> **Correction to revision 2.** Revision 2 made the same no-re-profiling
> claim, but the implementation did in fact re-read and re-profile the whole
> file, which showed as progress jumping back to `Ingesting`. It now does
> what the contract said. If you worked around the backwards jump, the
> workaround can go.

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

- `400` if the list is empty, or if a named column does not exist in the
  file, naming the offender. A rejected request starts nothing: the run stays
  in whatever state it was in.
- `409` if the run is neither `awaiting_cdes` nor `completed` — that is,
  still processing or failed.

**`cdeOverridden` means "changed", not "confirmed".** Every run now passes
through this endpoint, so it cannot simply mean "this endpoint was called".
It is `true` only when the confirmed set differs from what detection chose.
Confirming the detected set unchanged leaves it `false`.

**Suggested UI**, entirely at your discretion: a "Columns assessed: 18 of 34"
link on the summary opening a panel with a checkbox per column, the detected
reason as secondary text, and a "Re-assess" button. If this is too much for
the demo, `GET /runs/{id}/profile` alone as a read-only view would still be a
significant improvement, and we can add the override later.

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

> Backend note: `score` is computed once during the Scoring stage and read
> from stored results by both this endpoint and the run detail, so the two
> are the same number by construction rather than by coincidence.

#### [BACKEND PROPOSAL] Optional per-rule fields

```json
{ "id": "COM-01", "name": "Null rate within threshold", "passRate": 0.982,
  "column": "customer_id", "severity": "high", "evaluated": 128400, "failed": 2311 }
```

`column` is null for rules evaluated across the whole record, such as
duplicate detection. `severity` is one of `high`, `medium`, `low` and drives
the weighting inside the dimension score, so showing it explains why two
rules with similar pass rates move the score by different amounts.

Also: a dimension in `notAssessed` returns `score: null` and `rules: []`
here, with a `notAssessedReason` string.

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
- Return `404` if `ruleId` doesn't exist on that dimension.

> Backend note: implemented exactly as specified. Each rule writes its first
> 200 violations to a per-rule file as it executes, and `total` is that same
> execution's counter. The drawer, the examples and the report all read one
> set of numbers, so they cannot disagree. `limit` is capped server-side at
> 200; a higher value returns 200 rather than an error.

#### [BACKEND PROPOSAL] Example capture ceiling

`examples` is capped at the first 200 violations per rule, and they are the
first 200 **in file order**, not a random sample. Two implications for
wording in the UI: "Showing 10 of 5,136" is accurate, but a hypothetical
"show all" would top out at 200. If you want a full failing-record export we
would add `GET /runs/{id}/violations.csv` rather than raise this ceiling.

### `GET /runs/{id}/report?type=summary|in-depth`

Downloads a PDF report.

- `type` is exactly `summary` or `in-depth`.
- Return `409` if the run isn't `completed` yet — there's nothing to report
  on. The UI only ever shows this button on a completed run, but a direct API
  call shouldn't be able to crash report generation.

```
Content-Type: application/pdf
Content-Disposition: attachment; filename="customer_master_2024-summary.pdf"
```

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
- A run in `awaiting_cdes` is treated the same way (revision 4): it counts as
  unfinished work, so it is cancelled and removed rather than handled as a
  finished run.
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

## [BACKEND PROPOSAL] Summary of changes for review

| Change | Type | Frontend work if adopted |
| --- | --- | --- |
| `413` / `415` / `400` on upload | New error paths | None, existing error handling covers it |
| `columns`, `sampled`, `sampledRows`, `parseWarnings` on completed run | Additive fields | Optional, display only |
| `notAssessed` and `null` dimension scores | **Behavioural** | Tile must render null as "not assessed", not 0 |
| `GET /runs/{id}/profile` | New endpoint | Optional, new panel |
| `PUT /runs/{id}/cdes` | New endpoint | Optional, checkbox panel plus existing poll |
| `column`, `severity`, `evaluated`, `failed` per rule | Additive fields | Optional, display only |
| `awaiting_cdes` status; run pauses after Profiling | **Behavioural, rev 4** | New screen between progress and results; stop polling while parked; `PUT /cdes` to continue |
| `GET /profile` and `PUT /cdes` accept `awaiting_cdes` | **Behavioural, rev 4** | The confirm screen is built from these two |
| `cdeOverridden` false when the detected set is confirmed unchanged | **Behavioural, rev 4** | None unless you inferred "overridden" from having called `PUT /cdes` |
| `PUT /cdes` no longer re-profiles | **Correction, rev 4** | Progress no longer jumps back to `Ingesting`; drop any workaround |
| `integrity` as a seventh dimension | **Implemented, rev 3** | One entry in `constants.js`, grid tolerates 7, tile copy must not claim cross-system checks |
| `ACC-POSTCODE-COUNTRY` / `ACC-ZIP-STATE` renamed to `INT-*` | **Behavioural** | None unless rule ids are hard-coded; they are not in current `src/` |

Three items require frontend work to avoid a wrong result: the
`awaiting_cdes` screen, without which a new upload never reaches a score;
`notAssessed`; and the `integrity` tile — the last two because a dimension
that could not be assessed must not be drawn as a zero. Everything else is
optional.
