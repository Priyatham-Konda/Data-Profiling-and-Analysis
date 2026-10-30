# DQ Accelerator

A web application that measures the quality of data it has never seen
before. Upload a CSV export, or connect a Salesforce org, and it scores the
data across seven dimensions, shows every failing rule with example records,
and produces PDF reports a client can read. Column types, meaning and the
rules that apply are all inferred from the data itself: no schema, no setup
in the source system.

Built by dataskate. This repository holds the whole product: the React
frontend, the Python engine and API, and everything needed to run them in
production on AWS.

## What a user does

1. **Signs up or signs in.** Every account sees only its own work.
2. **Brings data in.** Uploads a CSV, TSV or text extract of up to 500 MB, or
   connects a Salesforce org and picks up to 25 objects.
3. **Confirms the critical data elements.** The engine profiles every column
   and proposes which ones matter (CDEs), with its reasons. Nothing is scored
   until a person confirms or edits that selection.
4. **Reads the results.** An overall score, a score per dimension, every rule
   with its pass rate, and the failing (and passing) records behind it.
5. **Takes it away.** A summary PDF, an in-depth PDF, and the exact data that
   was assessed, as CSV (or a ZIP per Salesforce assessment).

A dashboard compares the most recent results side by side.

## How the pieces fit

```
                         ┌──────────── one server (production) ─────────────┐
                         │                                                  │
 Browser ──── HTTPS ───▶ │  web: Caddy                                      │
                         │   ├─ /        → the React app (static files)     │
                         │   └─ /api/*   → api: FastAPI + the dqa engine ───┼──▶ Salesforce
                         │                     │                            │    (only when
                         │                     ▼                            │     connected)
                         │               /data volume: SQLite, uploads,     │
                         │               profiles, results, PDF reports     │
                         └──────────────────────────────────────────────────┘
```

| Part | Folder | Built with | Job |
| --- | --- | --- | --- |
| Frontend | `dq-accelerator-frontend/` | React 19, React Router 7, Vite 8, Tailwind CSS 4 | Sign-in, upload, CDE review, results, dashboard, Salesforce flow |
| Backend | `dq-accelerator-backend/` | Python 3.11, FastAPI, pandas, WeasyPrint | Accounts, the assessment pipeline, scoring, PDF reports, Salesforce connector |
| Web server | `dq-accelerator-frontend/Caddyfile` | Caddy 2 | HTTPS certificates, serves the app, forwards `/api` to the backend |
| Deployment | `deploy/`, `.github/workflows/` | Docker Compose, GitHub Actions, AWS | Runs both containers on one EC2 server |

The app and the API share one address in production, so the login cookie is
first-party and no cross-origin setup is needed. The contract between the two
halves is `API_CONTRACT.md` (revision 6); every endpoint the frontend calls
is described there, with its errors.

### The assessment pipeline

```
upload or Salesforce download
   └▶ Ingesting   encoding, delimiter, header detection
   └▶ Profiling   column statistics, type and meaning, CDE proposal
   ── pause: "Review CDEs" until a person confirms the selection ──
   └▶ Evaluating  every applicable rule, failing records captured
   └▶ Scoring     severity-weighted scores per dimension and overall
```

Work runs in background threads; the browser polls for progress and the user
never has to wait on a page. Files stream through in 50,000-row chunks, and
files over 5 million rows are sampled, with the sampling disclosed.

### The seven dimensions

| Dimension | Question it answers |
| --- | --- |
| Completeness | Are values present where required? |
| Validity | Does each value fit its format or allowed values? |
| Uniqueness | Does one entity appear twice? |
| Consistency | Is each field formatted the same way throughout? |
| Accuracy | Are values plausible and free of contradictions? |
| Timeliness | Is the data current? |
| Integrity | Do the relationships within the file hold? |

A dimension that cannot be judged (timeliness on a file with no dates, for
example) is reported as *not assessed*, never as zero. How each one is
measured is in `dq-accelerator-backend/README.md`.

## Repository layout

```
dq-accelerator-frontend/        the web app
  src/pages/                    one file per screen: AuthPage, UploadPanel, HomePanel,
                                SalesforcePanel, AssessmentPanel, DashboardPanel
  src/components/               app shell, sidebar, AuthProvider, RunsProvider, toasts
  src/api/                      the only place endpoints are called (client.js, auth.js, ...)
  src/mocks/                    an in-browser fake of the whole API (MSW), for working offline
  Dockerfile, Caddyfile         production image: the built app served by Caddy
dq-accelerator-backend/         the engine and API
  dqa/api/                      HTTP routes for runs and assessments
  dqa/auth/                     sign-up, sign-in, sessions, who owns what
  dqa/ingest, profiling, cde,   the pipeline stages
    checks, rules, scoring/
  dqa/report/                   PDF reports (Jinja2 + WeasyPrint)
  dqa/connectors/salesforce/    the Salesforce connector
  dqa/store/                    SQLite registry and run files
  rules/default_pack.yaml       the rules, as configuration
  tests/                        engine, API, auth and Salesforce tests
  Dockerfile                    production image of the API
deploy/
  docker-compose.prod.yml       both containers together, for one server
  .env.example                  the server's settings
.github/workflows/deploy-aws.yml  build, push to Amazon ECR, deploy on every merge to main
API_CONTRACT.md                 the frontend-backend contract (copies in each app folder)
REQUIREMENTS_CSV_PHASE1.md      phase 1 requirements
```

## Running it locally

You need **Node.js 22**, and for the backend **Python 3.11 or newer**.
**Docker Desktop** is optional, and is the easiest way to get working PDF
reports.

### Option A: frontend only, against the built-in mock (fastest)

```bash
cd dq-accelerator-frontend
npm ci
npm run dev
```

Open http://localhost:5173 and sign in as `demo@dataskate.ai` /
`dataskate123`, or sign up. The mock answers every API call in the browser
with realistic data, including the Salesforce flow. Mock accounts and runs
reset when the page is reloaded, and all mock accounts share the same runs.

### Option B: full stack, for development

Backend, in one terminal:

```bash
cd dq-accelerator-backend
python -m venv .venv
.venv\Scripts\activate            # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn dqa.api.app:app --reload --port 8000
```

Frontend, in a second terminal:

```bash
cd dq-accelerator-frontend
cp .env.local.example .env.local  # then uncomment VITE_API_BASE_URL and set it
                                  # to http://localhost:8000/api
npm ci
npm run dev
```

Open http://localhost:5173 and sign up. Setting `VITE_API_BASE_URL` switches
the mock off. Open the app with the **same host name** the variable uses
(both `localhost`, or both the same IP address); otherwise the browser
refuses the session cookie, and the sign-in page says so.

PDF reports need Pango, Cairo and the DejaVu fonts installed on the machine
when the backend runs outside Docker. Without them the PDF download fails
with a clear message, or its tables come out blank; the backend README
explains the fix for each platform.

### Option C: the production stack, in Docker

Exactly what runs on AWS: both images, Caddy in front, PDFs working.

```bash
cd deploy
cp .env.example .env              # then edit .env as below
docker compose -f docker-compose.prod.yml up -d --build
```

For a local run, set `SITE_ADDRESS=:80` and `DQA_COOKIE_SECURE=false` in
`.env`, then open http://localhost. The first build takes 5–10 minutes.
Stop it with `docker compose -f docker-compose.prod.yml down` (adding `-v`
also deletes every run and account).

## Accounts and privacy

- **Self sign-up** with name, email and password. No admin step.
- **Private by default.** Every run, Salesforce assessment and Salesforce
  connection belongs to the account that created it. Anyone else gets "not
  found", exactly as if it did not exist.
- **Sessions** are random tokens in an HttpOnly cookie, valid for 7 days. The
  database stores only a hash of each token; passwords are hashed with
  scrypt.
- **Upgrading an existing installation:** runs created before accounts
  existed go to the first account that signs up, so sign up first yourself.
- **Salesforce client secrets** are held in memory only for the duration of
  the download, never written to disk or logs, and the access token is
  revoked afterwards.
- **Runs are deleted after 7 days** by default (`DQA_RETENTION_DAYS`),
  uploaded file included.

## Configuration

Backend settings are environment variables; defaults suit local
development.

| Variable | Default | What it does |
| --- | --- | --- |
| `DQA_DATA_ROOT` | `./data` | Where the SQLite database and run files live |
| `DQA_MAX_WORKERS` | `2` | Assessments processed at the same time |
| `DQA_RETENTION_DAYS` | `7` | Days before a run and its files are deleted |
| `DQA_SESSION_DAYS` | `7` | How long a sign-in lasts |
| `DQA_COOKIE_SECURE` | `false` | Must be `true` whenever the site is served over HTTPS |
| `DQA_COOKIE_SAMESITE` | `lax` | `none` only if the app and the API are on different sites |
| `DQA_CORS_ORIGINS` | local dev addresses | Origins allowed to call the API directly |
| `DQA_API_PREFIX` | `/api` | Path the API is served under |
| `DQA_LOG_LEVEL` | `INFO` | Log detail |

Engine thresholds (CDE score, fuzzy matching, staleness window, outlier
sensitivity) are in `dq-accelerator-backend/dqa/config.py`.

The frontend has one setting, `VITE_API_BASE_URL`. Unset in development, the
in-browser mock answers; set, the app talks to that backend. Production
images leave it unset, so the app calls `/api` on its own address.

The production server's settings are in `deploy/.env.example`:
`SITE_ADDRESS` (the domain), `REGISTRY` and `IMAGE_TAG` (which images to
run), plus the backend variables above.

## Testing

| Part | Command | Covers |
| --- | --- | --- |
| Backend | `python tools/make_test_data.py --out tests/fixtures` then `pytest` | 220 tests: engine, API contract, accounts and ownership, Salesforce against a fake org. About 10 minutes |
| Frontend | `npm test` | 142 tests: screens, sign-in flow, mock rules |
| Frontend lint | `npm run lint` | ESLint |

Backend fixtures carry an exact, known number of seeded defects, and the
tests assert those exact counts rather than merely that the code ran.

Two known failures predate the login work: `test_scores_pool_every_object`
in `tests/test_salesforce.py` expects no `createdAt` field, which the API now
returns, and ESLint reports one error in `src/pages/home/DimensionDrawer.jsx`.

## Deployment

Production is one EC2 server running the two containers with Docker Compose,
with run data on the server's encrypted disk. Exactly one copy of the backend
may run, because it keeps SQLite, run files and open Salesforce connections
on that machine.

- **Method 1, Docker:** build both images on your machine, push them to
  Amazon ECR, then pull and start them on the server.
- **Method 2, GitHub:** `.github/workflows/deploy-aws.yml` runs the frontend
  tests, builds and pushes both images, and restarts them on the server
  through AWS Systems Manager on every merge to `main`. It signs in to AWS
  with GitHub OIDC, so no AWS keys are stored in GitHub. It needs three
  repository variables: `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN` and
  `EC2_INSTANCE_ID`.

Caddy obtains and renews the HTTPS certificate on its own once the domain
points at the server. The step-by-step guide, covering the AWS account
setup, both methods, backups, updates and troubleshooting:
[DQ Accelerator — Deploying to AWS](https://claude.ai/artifact/CryrL3aABcREQgFZSU3FPZ).

## Where to read more

| Document | For |
| --- | --- |
| `dq-accelerator-backend/README.md` | Engine internals: how each dimension is measured, run artifacts, thresholds, dependency licences |
| `dq-accelerator-backend/dqa/connectors/README.md` | Adding another source system next to Salesforce |
| `API_CONTRACT.md` | Every endpoint, request and response, and the Salesforce administrator's setup checklist |
| `dq-accelerator-frontend/FRONTEND.md` | The original UI build spec; the code has since moved on in places |
| `REQUIREMENTS_CSV_PHASE1.md` | Phase 1 requirements |

## Licences

Every dependency is permissively licensed: MIT, BSD or Apache 2.0, with
nothing for a client to purchase. The frontend's libraries (React, React
Router, Vite, Tailwind CSS, MSW) are MIT, and Caddy is Apache 2.0. The
backend's full list, and the two libraries excluded for their LGPL licence,
are in `dq-accelerator-backend/README.md`.

## Known limitations

1. **One server.** SQLite, files on disk and in-memory Salesforce
   connections tie the backend to a single machine. Running several needs
   Postgres and shared storage such as S3.
2. **No limit on sign-in attempts,** and no password reset or email
   verification yet.
3. **Accuracy without a source of truth is partial.** The engine checks
   reference lists, contradictions and statistical outliers, and the report
   says so rather than overclaiming.
4. **Integrity across datasets is not built yet:** orphaned foreign keys, or
   whether each Salesforce Contact's Account exists. Integrity today covers
   relationships within one file or object.
5. **Scores dilute on wide files.** Failures spread across many clean columns
   still produce a high dimension score; the rule-level numbers stay stark.

Next for Salesforce: a sign-in flow instead of entering credentials, checks
across the selected objects, and rules generated from Salesforce's own field
metadata. After that, SFTP and Oracle as further sources.
