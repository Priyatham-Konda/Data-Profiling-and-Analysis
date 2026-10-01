# DQ Accelerator — API contract

This is the exact contract the React frontend (`src/`) is built against. It's
reverse-derived from the frontend code and its MSW mock (`src/mocks/`), which
you can also read directly as a working reference implementation — every
endpoint below has a matching mock handler in `src/mocks/handlers.js`.

Point the frontend at a real implementation of this contract by setting
`VITE_API_BASE_URL` in `.env.local`. No frontend code changes are needed.

> **Revision 5 — Salesforce connections and multi-object assessments.**
> A user can connect a Salesforce org, pick objects from it, and get an
> assessment for each object plus an overall assessment across all of them,
> scored on the same seven dimensions. For now the connection uses three
> values the client's Salesforce administrator provides — the org's
> address, a client ID and a client secret; a sign-in flow will replace that
> dialog later without changing anything after it. Two items are marked
> **Next** and are not in this build. **Added since first handover:** the
> data each object was assessed on can be downloaded as CSV, per object or
> all at once — see *Downloading the assessed data*. File uploads and everything in
> revisions 1–4 are unchanged. Start at *Salesforce (revision 5)*.
>
> **Frontend status on this revision: adopted, against the mock.** The
> connect dialog, object picker, progress, per-object CDE tabs, overall
> results and both data downloads (**Download data (CSV)** on every run's
> results, uploads included; **Download all data (ZIP)** on an assessment's)
> are built (`src/pages/SalesforcePanel.jsx`, `src/pages/AssessmentPanel.jsx`,
> `src/pages/assessment/`), and every endpoint below has a handler in
> `src/mocks/handlers.js` backed by `src/mocks/salesforce.js`. The two
> **Next** items are not built. The dialog warns when `VITE_API_BASE_URL` is
> plain http on a non-local host, per *Before real client credentials are
> entered*.
>
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
  was added in revision 4; there were three before that.) Runs and
  assessments share these four values. A Salesforce connection has a
  separate `state` field with values of its own (revision 5) — named
  differently on purpose, so a status badge built for runs isn't reused for
  something that means something else.
- Timestamps are ISO 8601 in UTC: `"2026-09-28T10:14:03Z"`.
- Ids are opaque strings. Don't parse them or rely on their prefix.
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

> **Revision 5:** once *Integrity across objects* ships (marked **Next**),
> integrity inside a Salesforce assessment will check relationships between
> the selected objects, and the tile may then say so. Until then this
> paragraph applies to Salesforce assessments too.

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

**Revision 5:** lists standalone runs only. The per-object runs inside a
Salesforce assessment are listed through `GET /assessments` instead, so the
sidebar doesn't fill with one entry per object. Uploads are unaffected. See
*What changes on existing run endpoints*.

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

**Revision 5:** an optional `stageDetail` string may accompany `stage` —
for example `"Downloading from Salesforce: 3,200 of 5,790 records"`. Show it
under the stage name when present. Stage names themselves are unchanged.

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
- `409` for an object run of a Salesforce assessment that is still in
  `awaiting_cdes` — those are confirmed together through
  `PUT /assessments/{id}/cdes` (revision 5).

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
- Return `409` for an object run that belongs to a Salesforce assessment
  (revision 5): delete the assessment instead, which removes all its objects
  together and keeps the overall score coherent.

> Backend note: cancellation is checked between chunks, so a processing run
> stops within roughly one chunk of the delete call rather than running to
> completion in the background. All artifacts including the uploaded source
> file are removed.

## Salesforce (revision 5)

A user connects a Salesforce org, ticks the objects to assess, confirms the
critical data elements of each, and gets one result per object plus an
overall result across all of them.

```
[Connect Salesforce] ─▶ Connect dialog ─▶ Object picker ─▶ [Assess] ─▶ Progress ─▶ Confirm CDEs ─▶ Results
                         address, client    checkboxes                  one row per   one tab per     overall and
                         ID, client secret                              object        object          per object
```

Each ticked object becomes an ordinary **run** — the same run the upload
flow creates, reachable through every existing `/runs/{id}` endpoint. The
objects are grouped under an **assessment**, which carries the overall
result. So the per-object results view is the results view you already
have; what is new is the connect dialog, the picker, and the overall view.

### How the connection works, for now

The client's Salesforce administrator sets up a small app in their own org
for Salesforce's *client credentials* flow and hands over three values: the
org's address, the app's client ID and its client secret. The backend
exchanges them for an access token directly — there is no Salesforce sign-in
page and no browser redirect. *Setting up the credentials* below is the
administrator's checklist, and a good source for the dialog's help text.

This is interim. A sign-in flow, where the client just signs in on
Salesforce and clicks Allow, will later replace the connect dialog. Nothing
after the connection — picker, assessment, results — changes when it does.

### Screens to build

1. **Connect dialog.** Three fields, all required:
   - **Salesforce address** — the org's My Domain, or simply what the user
     copies from the browser's address bar while in Salesforce:
     `acme.my.salesforce.com`, `acme.lightning.force.com`, and the sandbox
     forms (`acme--uat.sandbox.my.salesforce.com`,
     `acme--uat.sandbox.lightning.force.com`) are all accepted, with or
     without `https://`. Production or sandbox is detected, not asked.
   - **Client ID** — Salesforce calls it the *consumer key*.
   - **Client secret** — Salesforce calls it the *consumer secret*. A
     password-type input; see *Handling the client secret*.

   On submit, call `POST /salesforce/connections`. It answers within a
   second or two: either the connection, or an error with an `errorCode`
   saying which field to look at. On success, show who the connection runs
   as — *"Connected to Acme Corporation (Enterprise Edition) as
   integration@acme.com"* — so a wrong org is caught before anything is
   downloaded, then move on to the picker.

2. **Object picker.** From `GET /salesforce/connections/{id}/objects`. A
   checkbox per object, a search box, a standard/custom filter, and the
   approximate record count. Highlight `suggested` objects but don't
   pre-tick them. At most 25 objects. **Assess** calls `POST /assessments`.

3. **Progress.** One row per object, all from a single poll of
   `GET /assessments/{id}`.

4. **Confirm CDEs.** Revision 4's confirm screen, with one tab per object,
   each built from `GET /runs/{runId}/profile`. One **Start analysis**
   button submits every tab at once through `PUT /assessments/{id}/cdes`.
   Objects that failed to download appear as a tab showing their `error`,
   with nothing to confirm.

5. **Results.** The overall tile grid from `GET /assessments/{id}`, next to
   a per-object list with each object's own scores. Clicking an object opens
   the existing single-run results view for its `runId` — dimension drawer,
   examples and per-object report all work unchanged.

6. **Data download buttons**, beside the report buttons. On each object's
   results view, **Download data (CSV)** → `GET /runs/{runId}/data`. On the
   assessment's results view, **Download all data (ZIP)** →
   `GET /assessments/{id}/data`. See *Downloading the assessed data*.

### Handling the client secret

The secret gives whoever holds it read access to the client's Salesforce.
In the frontend:

- Never put it in `localStorage`, `sessionStorage`, the URL, logs, analytics
  or error reports. Send it once, in the body of `POST /salesforce/connections`.
- Clear the field after submitting, successful or not.
- Use `autocomplete="off"` so the browser doesn't offer to save it.

On the backend it is never returned by any endpoint, never written to disk
or logs, and held in memory only until the selected objects have been
downloaded — then it is discarded and the access token revoked.

### Setting up the credentials

For the client's Salesforce administrator. Takes about five minutes.

1. **Setup → App Manager → New External Client App.** Give it a name
   (for example *DQ Accelerator*), a contact email, and Distribution State
   **Local**.
2. Under **API (Enable OAuth Settings)**, tick **Enable OAuth Settings**.
   Enter any valid **Callback URL** — `https://localhost/callback` is fine;
   this flow never uses it, but Salesforce requires one.
3. Under **OAuth Scopes**, select **Manage user data via APIs (api)** only.
4. Under **Flow Enablement**, tick **Enable Client Credentials Flow**, then
   save.
5. On the **Policies** tab, choose **Edit**. Under **OAuth Policies**, tick
   **Enable Client Credentials Flow** again and enter the **Run As
   (Username)**: the user whose access defines what gets assessed. Save.
6. Under **Settings → OAuth Settings**, choose **Consumer Key and Secret**
   and copy both.
7. The org's address is under **Setup → My Domain**.

**Choose the Run As user with care.** Salesforce has no read-only scope:
the app can do whatever the Run As user can. The backend only ever reads,
but the guarantee that nothing *can* be changed comes from that user's
permissions. The right choice is a dedicated integration user with read
access to the objects to be assessed and no edit rights. Only objects and
fields that user can see are assessed.

Salesforce can take a few minutes after saving before new credentials work;
`invalid_client` straight after setup usually just means waiting.

### Connection lifecycle

```
POST ─▶ connected ──(records downloaded │ DELETE │ 30 min unused │ server restart)──▶ closed
```

- **A failed attempt creates nothing.** `POST` returns the error and keeps
  nothing — not the connection, not the secret.
- **A connection serves exactly one assessment.** Once the selected objects
  have been downloaded, the secret is discarded, the access token revoked,
  and the connection moves to `closed` with `closedReason: "extracted"`.
  **This is normal, not an error** — don't present it as a disconnection.
  Scoring, CDE changes and reports all work from the downloaded data and
  never need Salesforce again.
- **Assessing the same org again means entering the credentials again.**
  The dialog can't pre-fill the secret, deliberately.
- A connection nobody uses closes after 30 minutes (`closedReason:
  "idle"`); `expiresAt` says when.
- Connections live in memory, so a backend restart closes them all. Their
  ids then return `404`: ask the user to connect again. Assessments are
  unaffected.

### `POST /salesforce/connections`

- **Request:**

```json
{
  "instanceUrl": "acme.lightning.force.com",
  "clientId": "<consumer key from the External Client App>",
  "clientSecret": "<consumer secret from the External Client App>"
}
```

- **Response:** `201` with the connection — the same shape as
  `GET /salesforce/connections/{id}`, below.

- **Errors.** Every error body carries an `errorCode` for branching and an
  `error` message written for the user:

```json
{
  "errorCode": "flow_not_enabled",
  "error": "The app at acme.my.salesforce.com isn't set up for the client credentials flow. In the app's settings, tick Enable Client Credentials Flow under Flow Enablement and again under the OAuth Policies."
}
```

| HTTP | `errorCode` | Meaning | Point the user at |
| --- | --- | --- | --- |
| `400` | `invalid_url` | Not a Salesforce address. Only Salesforce's own domains are accepted — a security control, because the backend sends the secret to that address | Salesforce address |
| `400` | — | A field is missing or empty (the standard validation error) | That field |
| `422` | `invalid_client` | Salesforce rejected the client ID or secret | Client ID and secret; they may also need a few minutes after setup |
| `422` | `flow_not_enabled` | The app isn't enabled for the client credentials flow | Setup steps 4 and 5 |
| `422` | `no_run_as_user` | The app has no Run As user | Setup step 5 |
| `422` | `api_disabled` | The org or the Run As user has no API access — typical of Professional and Essentials editions without the API add-on | The client's administrator |
| `502` | `unreachable` | Nothing answered at that address | Salesforce address, or the network |
| `422` | `failed` | Salesforce refused for a reason not listed above; its own words are in `error` | Show `error` as it is |

### `GET /salesforce/connections/{id}`

```json
{
  "id": "sfc_q8Zt3mW1xR5v",
  "state": "connected",
  "environment": "production",
  "orgId": "00D5g000004XyZAEA0",
  "orgName": "Acme Corporation",
  "orgEdition": "Enterprise Edition",
  "instanceUrl": "https://acme.my.salesforce.com",
  "username": "integration@acme.com",
  "connectedAt": "2026-09-28T10:14:03Z",
  "expiresAt": "2026-09-28T10:44:03Z",
  "closedReason": null,
  "assessmentId": null
}
```

| Field | Meaning |
| --- | --- |
| `state` | `connected` or `closed` |
| `environment` | `production` or `sandbox`, detected from the org |
| `username` | The Run As user every API call is made as |
| `expiresAt` | When an unused connection closes. `null` once an assessment has started |
| `closedReason` | `extracted`, `disconnected` or `idle` when `closed`, else `null` |
| `assessmentId` | The assessment this connection feeds, once one has started |

The secret is never returned, by this or any endpoint. One call after
connecting is enough; this isn't polled.

### `GET /salesforce/connections/{id}/objects`

The objects the user can pick from.

```json
{
  "objects": [
    { "name": "Account",     "label": "Account",     "custom": false, "recordCount": 5790,  "suggested": true },
    { "name": "Contact",     "label": "Contact",     "custom": false, "recordCount": 1500,  "suggested": true },
    { "name": "Invoice__c",  "label": "Invoice",     "custom": true,  "recordCount": 12044, "suggested": false },
    { "name": "Opportunity", "label": "Opportunity", "custom": false, "recordCount": null,  "suggested": true }
  ],
  "hiddenCount": 612
}
```

- **Only objects the Run As user can read are listed**, and fields hidden
  from that user aren't downloaded.
- **Salesforce's system objects are hidden by default** — history, sharing,
  feed, change-event and setup objects, typically several hundred of them.
  `hiddenCount` says how many; `?include=all` returns them too.
- **`recordCount` is Salesforce's own approximate figure** and may be
  `null`. Show it as "about 5,790", not "5,790".
- Show `label` as the primary text and `name` as secondary: custom objects'
  API names (`Invoice__c`) mean little to a business user.
- Takes one to three seconds. Fetch it once per connection, don't poll.
- `404` if the connection doesn't exist; `409` unless it is `connected`.

### `DELETE /salesforce/connections/{id}`

Revokes the access token and discards the secret. The connection becomes
`closed` with `closedReason: "disconnected"`. Returns `204`, or `404` if it
doesn't exist. Returns `409` while an assessment is still downloading from
it — delete the assessment instead, which stops the download.

### `POST /assessments`

Start assessing the selected objects.

- **Request:**

```json
{
  "source": { "type": "salesforce", "connectionId": "sfc_q8Zt3mW1xR5v" },
  "objects": ["Account", "Contact", "Invoice__c"]
}
```

`source.type` is `"salesforce"`, the only multi-object source today. File
uploads stay on `POST /runs`. `objects` holds object `name`s, not labels.

- **Response:** `201`, returned as soon as the work is queued.

```json
{
  "id": "asm_7Kp2Vd9nLc4e",
  "name": "Acme Corporation",
  "status": "processing",
  "runs": [
    { "id": "run_260928_a1c2", "object": "Account",    "label": "Account", "status": "processing" },
    { "id": "run_260928_b7d0", "object": "Contact",    "label": "Contact", "status": "processing" },
    { "id": "run_260928_c3e9", "object": "Invoice__c", "label": "Invoice", "status": "processing" }
  ]
}
```

`name` is the org's name, with `" (Sandbox)"` appended for a sandbox.

- `400` if `objects` is empty, has more than 25 entries, repeats an object,
  or names one not in the connection's list (the `error` names it), or if
  `source.type` isn't `"salesforce"`.
- `404` if the connection doesn't exist; `409` if it isn't `connected` or
  already feeds another assessment.

### `GET /assessments`

All assessments, newest first. The Salesforce counterpart of `GET /runs`,
cheap in the same way and polled the same way — every 5 seconds while any
assessment is `processing`.

```json
[
  { "id": "asm_7Kp2Vd9nLc4e", "name": "Acme Corporation",   "status": "completed",  "overall": 85.5, "objects": 3 },
  { "id": "asm_3Hs8Qx1bWe7t", "name": "Globex (Sandbox)",   "status": "processing", "objects": 5 }
]
```

`overall` is present only when `status` is `"completed"`.

### `GET /assessments/{id}`

Full detail, with one entry per object. Poll every 3 seconds while
`processing`, and **stop at `awaiting_cdes`**, `completed` and `failed`,
exactly as for a run.

Each entry in `runs` has **the same shape `GET /runs/{id}` returns for that
run's status**, without `file` and with `object` and `label` added. One poll
here therefore replaces polling each object.

**Processing:**

```json
{
  "id": "asm_7Kp2Vd9nLc4e",
  "name": "Acme Corporation",
  "status": "processing",
  "source": {
    "type": "salesforce",
    "orgName": "Acme Corporation",
    "orgId": "00D5g000004XyZAEA0",
    "environment": "production"
  },
  "progress": 0.38,
  "runs": [
    { "id": "run_260928_a1c2", "object": "Account", "label": "Account",
      "status": "processing", "stage": "Ingesting", "stageIndex": 0,
      "stageCount": 4, "progress": 0.55,
      "stageDetail": "Downloading from Salesforce: 3,200 of 5,790 records" },
    { "id": "run_260928_b7d0", "object": "Contact", "label": "Contact",
      "status": "awaiting_cdes", "records": 1500, "columns": 69, "cdes": 22 },
    { "id": "run_260928_c3e9", "object": "Invoice__c", "label": "Invoice",
      "status": "processing", "stage": "Ingesting", "stageIndex": 0,
      "stageCount": 4, "progress": 0.0, "stageDetail": "Queued" }
  ]
}
```

`progress` is one 0–1 figure across all objects for the current phase —
use it for a single overall bar and the rows for detail. Downloading from
Salesforce is reported as the `Ingesting` stage, with `stageDetail` saying
what is happening: `"Queued"`, `"Waiting for Salesforce to prepare the
export"`, or a record count.

**How the assessment's status follows its objects:**

| Assessment `status` | When |
| --- | --- |
| `processing` | Any object is still `processing` |
| `awaiting_cdes` | No object is processing, and at least one is `awaiting_cdes` |
| `completed` | Every object is `completed` or `failed`, and at least one is `completed` |
| `failed` | Every object `failed` |

One failed object doesn't fail the assessment. It is reported with its own
`error` — typically a permission problem on that object, or an object with
no records — and the rest carry on.

**Awaiting CDEs:** the same wrapper, `status: "awaiting_cdes"`, no
`progress`, and each object run in its revision-4 `awaiting_cdes` shape (or
`failed`). Fetch `GET /runs/{runId}/profile` for each tab of the confirm
screen.

**Completed:**

```json
{
  "id": "asm_7Kp2Vd9nLc4e",
  "name": "Acme Corporation",
  "status": "completed",
  "source": { "...": "as above" },
  "overall": 85.5,
  "records": 7277,
  "objects": 3,
  "partial": true,
  "scores": {
    "completeness": 26.3, "validity": 99.4, "uniqueness": 98.3,
    "consistency": 92.2, "accuracy": 83.1, "timeliness": 100.0,
    "integrity": 99.2
  },
  "notAssessed": {},
  "runs": [
    { "id": "run_260928_a1c2", "object": "Account", "label": "Account",
      "status": "completed", "overall": 80.7, "records": 5777, "cdes": 18,
      "scores": { "...": "all seven keys" } },
    { "id": "run_260928_b7d0", "object": "Contact", "label": "Contact",
      "status": "completed", "overall": 93.1, "records": 1500, "cdes": 22,
      "scores": { "...": "all seven keys" } },
    { "id": "run_260928_c3e9", "object": "Invoice__c", "label": "Invoice",
      "status": "failed",
      "error": "Salesforce refused to return Invoice records to the Run As user." }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `records` | Total records across the completed objects |
| `objects` | How many objects were selected, including any that failed |
| `partial` | `true` when at least one object failed. The overall covers the completed objects only, and the UI must say so: *"Overall covers 2 of 3 objects — Invoice could not be assessed."* |
| `scores`, `notAssessed` | As for a run, across all completed objects — see *The overall score* |

### The overall score

Each overall dimension score is computed exactly as a single run's is — the
severity-weighted pass rate — but across every rule of every completed
object, as if the objects were one dataset. `overall` is then the mean of
the scored dimensions, again as for a run.

The consequence to design around: **an object weighs in proportion to how
much of it was checked.** A two-million-row Task object dominates a
five-thousand-row Account. That is the honest answer to "how clean is
everything we checked", but it can bury a small object in poor shape. Show
the per-object scores beside the overall, never the overall on its own.

A dimension is scored overall if at least one object could assess it;
objects that couldn't simply contribute nothing to it. In the example,
timeliness is `100.0` from Contact alone, because Account had no dated
critical data elements. The per-object list shows that. A dimension appears
in the assessment's `notAssessed` only when no object could assess it.

### What gets downloaded

Every field of each selected object that the Run As user can read, and
every record, fetched with SOQL. Large objects go through Salesforce's Bulk
API; the few objects it doesn't support go through the ordinary query API
instead, with the same result. Two exceptions, both deliberate:

- **Addresses arrive as their component fields** — `BillingCity`,
  `BillingPostalCode` and so on — not also as a compound `BillingAddress`,
  so the same value is never assessed twice.
- **File contents** (attachment bodies and similar binary fields) are not
  downloaded.

Objects over 5,000,000 records are sampled after download, exactly as large
files are, and disclosed the same way.

### Integrity across objects — **Next**

Not in this build. Once it ships, a rule `INT-REFERENCE-{nn}` bound to a
reference field — say `Contact.AccountId` — will check that each value
resolves to a record of the parent object *in the same assessment*, and
list the orphans. It will run only when the parent object was selected too
and the reference field was confirmed as a CDE. The `referenceTo` field in
the profile's `salesforce` block, below, is already there for the confirm
screen to show which relationships a selection makes possible. No other
frontend work: the rule arrives through the existing integrity drawer.

### Rules from Salesforce metadata — **Next**

Not in this build. Salesforce declares each field's type, whether it is
required, its picklist values and its maximum length; rules built from
those — *"Required in Salesforce"*, *"Value is one of the picklist
values"* — will arrive through the existing drawer, with nothing to build.
Until then, object runs are assessed exactly as uploaded files are: every
expectation inferred from the data.

### `PUT /assessments/{id}/cdes`

Confirm every object's CDEs at once and start scoring. The per-object
revision-4 rules apply to each list.

- **Request:**

```json
{
  "objects": {
    "Account": ["Name", "Phone", "BillingCity", "BillingPostalCode", "Industry"],
    "Contact": ["FirstName", "LastName", "Email", "MailingPostalCode", "AccountId"]
  }
}
```

- **Response:** `202`, with the assessment back to `processing`. Resume
  polling `GET /assessments/{id}`.

```json
{ "id": "asm_7Kp2Vd9nLc4e", "status": "processing" }
```

- **From `awaiting_cdes`,** every object in `awaiting_cdes` must be
  present. Failed objects are left out.
- **From `completed`,** include only the objects to re-score; the rest keep
  their results, and the overall is recomputed once the re-scored objects
  finish. A single completed object can also be re-scored through
  `PUT /runs/{runId}/cdes`, with the same effect on the overall.
- `400` if a list is empty, names a column not in that object, names an
  object not in the assessment or not ready to score, or — from
  `awaiting_cdes` — leaves an awaiting object out. The `error` names the
  object and column. A rejected request starts nothing.
- `409` if the assessment is `processing` or `failed`.

### `GET /assessments/{id}/report?type=summary|in-depth`

One PDF for the whole assessment: the overall result, the ranked
*What this means* across all objects, and a section per object; the
in-depth report adds each object's rule breakdown and failing-record
examples. Each object's own report is still available from
`GET /runs/{runId}/report`.

Same rules as the run report: `409` unless `completed`, and the download is
a plain `<a href download>`, with the same auth constraint.

### `DELETE /assessments/{id}`

Stops any work in progress, including a download from Salesforce, closes
the connection if it is still open, and removes every object run and all
downloaded data. `204`, or `404` if it doesn't exist.

### Downloading the assessed data

The records of each object exactly as they came down from Salesforce — the
very file that profiling and scoring ran on. Two download buttons, both
plain `<a href download>` links like the report, with the same auth
constraint:

| Button | Where | Endpoint | Returns |
| --- | --- | --- | --- |
| **Download data (CSV)** | Each object's results view | `GET /runs/{runId}/data` | That object's CSV |
| **Download all data (ZIP)** | The assessment's results view | `GET /assessments/{id}/data` | One CSV per object, zipped |

#### `GET /runs/{id}/data`

```
Content-Type: text/csv; charset=utf-8
Content-Disposition: attachment; filename="Acme_Corporation_Account.csv"
```

- **Every field of every record**, as downloaded, with `Id` first — not
  only the confirmed CDEs. Profiling looks at every column; the CDE
  selection only decides which columns are scored, so the full file is the
  one the assessment used.
- For a Salesforce object the file starts with a UTF-8 byte-order mark,
  which makes Excel read accented names correctly; beyond that it is byte
  for byte the downloaded data.
- For an uploaded file it returns the upload itself, unchanged. The button
  is required on Salesforce objects; showing it on uploads is your choice.
- **Available once profiling has finished** — from `awaiting_cdes` onward,
  including while a completed run is being re-scored. `409` before that,
  because the download may still be in progress; `404` if the run doesn't
  exist.

#### `GET /assessments/{id}/data`

```
Content-Type: application/zip
Content-Disposition: attachment; filename="Acme_Corporation-data.zip"
```

- **One CSV per object**, named by the object's API name — `Account.csv`,
  `Contact.csv`, `Invoice__c.csv` — each exactly what `GET /runs/{id}/data`
  returns for it. Objects that failed to download aren't included.
- **Available from `awaiting_cdes` onward.** `409` while any object is
  still downloading or profiling for the first time, or when no object has
  data to give (every object failed); `404` if the assessment doesn't
  exist.
- The first request builds the file, which can take several seconds on a
  large org. Later requests are immediate.

#### Two things the UI copy should reflect

- **This is the client's own data**, every field the Run As user could see —
  typically names, emails and phone numbers. The file is theirs, not ours;
  a short line under the button saying so is worth having.
- **Values are exactly as stored in Salesforce, not escaped.** A spreadsheet
  may treat a value beginning with `=`, `+`, `-` or `@` as a formula when
  the file is opened. Escaping would stop the file being the data that was
  assessed, so it is left as it is.

### What changes on existing run endpoints

| Endpoint | Change |
| --- | --- |
| `GET /runs` | Lists standalone runs only. Object runs belonging to an assessment are listed through `GET /assessments`. `?include=all` adds them, each with `assessmentId`. Uploads are unaffected. |
| `GET /runs/{id}` | Object runs carry `assessmentId` and `source` (`{ "type": "salesforce", "orgName", "object", "label" }`) in every status, and `file` reads `"Acme Corporation · Account"` so existing views render sensibly. **Upload runs carry neither field; their shapes are exactly as in revision 4.** |
| `GET /runs/{id}` while processing | Optional `stageDetail`. Stage names are unchanged. |
| `GET /runs/{id}/profile` | Object runs add a `salesforce` block to each column (below). |
| `PUT /runs/{id}/cdes` | `409` on an object run in `awaiting_cdes`. Allowed on a completed object run: it re-scores that object and the assessment's overall is recomputed, the assessment reporting `processing` meanwhile. |
| `DELETE /runs/{id}` | `409` on an object run. |
| `GET /runs/{id}/report` | Works on an object run: that object's report on its own. |
| `GET /runs/{id}/data` | **New.** The run's data as CSV — see *Downloading the assessed data*. Works on uploads too. |

The `salesforce` block on each profile column:

```json
{
  "name": "AccountId",
  "inferredType": "string",
  "semanticType": "identifier",
  "...": "the other revision-2 fields",
  "salesforce": {
    "label": "Account ID",
    "type": "reference",
    "custom": false,
    "required": false,
    "referenceTo": ["Account"],
    "picklistValues": null,
    "length": 18
  }
}
```

For the confirm screen: show `label` first with the API `name` beside it
(`Total_Amount_Debt__c` means little; "Total Amount of Debt" means
something), badge `required` fields, and badge reference fields with their
target — *"→ Account"*. `picklistValues` is a list of strings for picklist
fields and `null` otherwise.

### Mocking the flow

Every Salesforce call is now an ordinary request and response, so MSW
mocks it like any other endpoint. Mock `POST /salesforce/connections` to
return a connection, and — to build the error states — each `errorCode` in
the table above. `invalid_client` and `flow_not_enabled` are the two a
sales rep will see most.

### Before real client credentials are entered

Two backend prerequisites that affect how the frontend is deployed:

1. **HTTPS.** The client secret travels from the browser to the backend in
   a request body. Over plain HTTP — `http://192.168.x.x:8000`, as today —
   anyone on the network can read it. `VITE_API_BASE_URL` must be an HTTPS
   address before a real client's credentials go into the dialog.
2. **Authentication.** The API still has none. With uploads, that exposes
   uploaded files to anyone who can reach the API. With Salesforce it
   exposes an open connection into a client's CRM, and the downloaded
   objects after — and with the data downloads, anyone who can reach the
   API can take every object's full records in one request. Short-lived
   connections and unguessable ids narrow that, but authentication is
   planned for revision 6: expect an `Authorization` header on every call,
   and signed URLs for the report and data downloads.

## Three things likely to bite

1. **`GET /runs` and `GET /runs/{id}` are polled continuously**, per open
   browser tab, for as long as anything is in flight. Avoid heavy joins on
   either — this is the path most likely to show up in a slow-query log.
2. **Rule examples must reflect the actual failing rows**, not be
   regenerated per request. If the pass rate for a rule is 96%, the 10
   examples returned should be real violations from that run, and `total`
   should be the real count — not derived independently of each other,
   or a user will notice the drawer, the report, and the examples disagreeing.
3. **The client secret must not outlive the request** (revision 5). It is
   the one value in this API that grants access to someone else's system.
   Keep it out of browser storage, URLs, logs and error reports, and clear
   the field after submitting — see *Handling the client secret*.

## [BACKEND PROPOSAL] Summary of changes for review

| Change | Type | Frontend work if adopted |
| --- | --- | --- |
| `413` / `415` / `400` on upload | New error paths | None, existing error handling covers it |
| `columns`, `sampled`, `sampledRows`, `parseWarnings` on completed run | Additive fields | Optional, display only |
| `notAssessed` and `null` dimension scores | **Behavioural** | Tile must render null as "not assessed", not 0 |
| `GET /runs/{id}/profile` | New endpoint | Optional, new panel |
| `PUT /runs/{id}/cdes` | New endpoint | Optional, checkbox panel plus existing poll |
| `column`, `severity`, `evaluated`, `failed` per rule | Additive fields | Optional, display only |
| Salesforce connection from client credentials, object picker, multi-object assessment | **New, rev 5** | Connect dialog, object picker, per-object progress and confirm tabs, overall results view |
| `GET /runs` omits object runs of assessments | **Behavioural, rev 5** | None for uploads; list assessments from `GET /assessments` |
| `source`, `assessmentId` on object runs; `stageDetail` while processing | **Additive, rev 5** | Optional, display only; upload runs unchanged |
| `salesforce` block on profile columns | **Additive, rev 5** | Optional, but makes the confirm screen far clearer |
| `GET /runs/{id}/data`, `GET /assessments/{id}/data` | **New, rev 5** | Two download buttons beside the report buttons |
| Integrity across objects; rules from Salesforce metadata | **Next** | None when they ship, beyond integrity tile copy |
| `awaiting_cdes` status; run pauses after Profiling | **Behavioural, rev 4** | New screen between progress and results; stop polling while parked; `PUT /cdes` to continue |
| `GET /profile` and `PUT /cdes` accept `awaiting_cdes` | **Behavioural, rev 4** | The confirm screen is built from these two |
| `cdeOverridden` false when the detected set is confirmed unchanged | **Behavioural, rev 4** | None unless you inferred "overridden" from having called `PUT /cdes` |
| `PUT /cdes` no longer re-profiles | **Correction, rev 4** | Progress no longer jumps back to `Ingesting`; drop any workaround |
| `integrity` as a seventh dimension | **Implemented, rev 3** | One entry in `constants.js`, grid tolerates 7, tile copy must not claim cross-system checks |
| `ACC-POSTCODE-COUNTRY` / `ACC-ZIP-STATE` renamed to `INT-*` | **Behavioural** | None unless rule ids are hard-coded; they are not in current `src/` |

Three items require frontend work to avoid a wrong result: the
`awaiting_cdes` screen, without which a new upload never reaches a score;
`notAssessed`; and the `integrity` tile — the last two because a dimension
that could not be assessed must not be drawn as a zero. Revision 5 is new
work rather than a change to existing screens: the Salesforce flow, plus two
rules — the overall score is never shown without the per-object scores
beside it, and the client secret never outlives its request. Everything else
is optional.
