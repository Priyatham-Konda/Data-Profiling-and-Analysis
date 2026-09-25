import {
  STATUS,
  STAGES,
  DIMENSIONS,
  MAX_UPLOAD_BYTES,
  ACCEPTED_UPLOAD_EXTENSIONS,
} from '@/api/constants';

// In-memory stand-in for the dqa engine. A processing run's stage is DERIVED
// from elapsed time rather than mutated by a timer, so every poll during the
// same tick returns the same answer and nothing drifts if the tab sleeps.

const STAGE_MS = 3000;

// A run auto-advances through the first half of STAGES (Ingesting,
// Profiling) and then PAUSES at status: 'awaiting_cdes' with its
// auto-detected CDEs, instead of continuing straight to Evaluating +
// Scoring. Nothing scores a file until a human confirms which columns
// matter -- see PUT /runs/{id}/cdes (applyCdeOverride below), which is the
// only thing that ever moves a run through the second half.
const PROFILE_STAGES = STAGES.slice(0, 2);
const SCORING_STAGES = STAGES.slice(2);

function stagesFor(run) {
  return run.processingKind === 'scoring' ? SCORING_STAGES : PROFILE_STAGES;
}

function totalMsFor(run) {
  return STAGE_MS * stagesFor(run).length;
}

// Deterministic PRNG so a completed run's scores never change between polls.
function hash(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function rng(seed) {
  let t = seed;
  return () => {
    t += 0x6d2b79f5;
    let x = Math.imul(t ^ (t >>> 15), 1 | t);
    x ^= x + Math.imul(x ^ (x >>> 7), 61 | x);
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}

// Scores deliberately span all three bands so the tile colours get exercised.
function buildScores(seed) {
  const rand = rng(hash(seed));
  const targets = [96, 91, 84, 77, 68, 61];
  const scores = {};
  DIMENSIONS.forEach((dim, i) => {
    const base = targets[i % targets.length];
    scores[dim.key] = Math.round((base + (rand() * 8 - 4)) * 10) / 10;
  });
  return scores;
}

// Excludes not-assessed (null) dimensions from the average, same as the real
// engine would -- a dimension nobody could score shouldn't drag the overall
// down, and it definitely shouldn't count as a 0.
function overallOf(scores) {
  const values = Object.values(scores).filter((v) => typeof v === 'number');
  return Math.round((values.reduce((a, b) => a + b, 0) / values.length) * 10) / 10;
}

// Human-readable reasons per dimension, for filenames that trigger the
// not-assessed path below. Per API_CONTRACT.md, integrity is expected to hit
// this "far more often than any other dimension" on a real backend, since it
// infers relationships rather than being told them -- the mock only flips it
// via an explicit trigger (like timeliness) to keep runs deterministic and
// testable, not by default on every run.
const NOT_ASSESSED_REASONS = {
  timeliness: 'No date column was detected in this file.',
  integrity:
    'Integrity compares fields against one another, and fewer than two populated critical data elements were found in this file.',
};

function parseWarningsFor(run) {
  const rand = rng(hash(`${run.id}:warnings`));
  return Math.floor(rand() * 15);
}

let seq = 0;
function nextId() {
  seq += 1;
  return `run_${String(Date.now()).slice(-6)}${seq}`;
}

function seeded({ id, file, status, error, minutesAgo }) {
  return {
    id,
    file,
    status,
    error,
    createdAt: new Date(Date.now() - minutesAgo * 60_000).toISOString(),
    startedAt: Date.now() - minutesAgo * 60_000,
    records: 128_400,
    cdes: 18,
  };
}

const runs = [
  seeded({ id: 'run_240118', file: 'customer_master_2024.csv', status: STATUS.COMPLETED, minutesAgo: 14 }),
  seeded({ id: 'run_240117', file: 'transactions_q3.csv', status: STATUS.COMPLETED, minutesAgo: 68 }),
  seeded({
    id: 'run_240116',
    file: 'vendor_feed_raw.csv',
    status: STATUS.FAILED,
    error: 'Could not parse row 4,812: expected 18 columns, found 21. Check the delimiter and any unescaped commas in free-text fields.',
    minutesAgo: 155,
  }),
];

export function createRun(filename) {
  // Any filename containing "fail"/"bad" lands in the failed state, "nodate"
  // and "narrow" each trigger a not-assessed dimension (and can combine --
  // a filename matching both), and "sampled"/"huge" simulates a file over
  // the row cap -- all so those paths are reachable on demand rather than by
  // luck, without needing a real multi-million-row or single-column file.
  const sampled = /sampled|huge/i.test(filename);
  const notAssessedDimensions = [];
  if (/nodate|no-timeliness/i.test(filename)) notAssessedDimensions.push('timeliness');
  if (/narrow|no-integrity/i.test(filename)) notAssessedDimensions.push('integrity');

  const run = {
    id: nextId(),
    file: filename,
    willFail: /fail|bad|corrupt/i.test(filename),
    notAssessedDimensions,
    status: STATUS.PROCESSING,
    createdAt: new Date().toISOString(),
    startedAt: Date.now(),
    records: sampled ? 6_200_000 : 40_000 + Math.floor(Math.random() * 200_000),
    sampled,
    sampledRows: sampled ? 40_000 + Math.floor(Math.random() * 200_000) : null,
    cdes: 8 + Math.floor(Math.random() * 20),
    cdeOverridden: false,
  };
  runs.unshift(run);
  return run;
}

// A run can have more than one not-assessed dimension at once (a filename
// can match both the "nodate" and "narrow" triggers), so this nulls out
// every flagged key rather than just one, building the notAssessed map in
// the same pass.
function applyNotAssessed(run) {
  if (!run.notAssessedDimensions?.length) return;
  run.notAssessed = {};
  run.notAssessedDimensions.forEach((dim) => {
    run.scores[dim] = null;
    run.notAssessed[dim] = NOT_ASSESSED_REASONS[dim];
  });
}

// Resolves a stored run into its current state. Only ever advances a run
// that is STATUS.PROCESSING -- awaiting_cdes, completed, and failed are all
// resting states that require an explicit action (confirm, delete, re-run)
// to leave, not the mere passage of time.
function resolve(run) {
  if (run.status !== STATUS.PROCESSING) return run;

  const totalMs = totalMsFor(run);
  const elapsed = Date.now() - run.startedAt;
  if (elapsed < totalMs) return run;

  if (run.processingKind === 'scoring') {
    // Evaluating + Scoring just finished. This is the ONLY place scores get
    // computed -- whether this is the very first scoring pass (right after
    // confirming CDEs out of awaiting_cdes) or a later re-assessment of an
    // already-completed run. willFail and any not-assessed dimensions are
    // properties of the file itself, not of which columns were chosen as
    // CDEs, so they carry over from Ingesting/Profiling untouched.
    run.scores = buildScores(`${run.id}:${[...run.overrideColumns].sort().join(',')}`);
    applyNotAssessed(run);
    run.overall = overallOf(run.scores);
    run.status = STATUS.COMPLETED;
    run.processingKind = undefined;
    return run;
  }

  // Ingesting + Profiling just finished.
  if (run.willFail) {
    run.status = STATUS.FAILED;
    run.error =
      'Profiling stopped: 3 of 18 candidate CDEs had no non-null values, so no baseline could be established.';
    return run;
  }

  // Pause here with the auto-detected CDEs (run.cdes, set at creation) for
  // the user to review. Nothing scores until PUT /runs/{id}/cdes confirms a
  // selection -- see applyCdeOverride, which is what actually starts the
  // Evaluating + Scoring pass above.
  run.status = STATUS.AWAITING_CDES;
  return run;
}

function ensureScores(run) {
  if (run.status === STATUS.COMPLETED && !run.scores) {
    run.scores = buildScores(run.id);
    run.overall = overallOf(run.scores);
  }
  return run;
}

export function allRuns() {
  return runs.map(resolve).map(ensureScores);
}

export function findRun(id) {
  const run = runs.find((r) => r.id === id);
  return run ? ensureScores(resolve(run)) : null;
}

export function removeRun(id) {
  const index = runs.findIndex((r) => r.id === id);
  if (index === -1) return false;
  runs.splice(index, 1);
  return true;
}

export function summaryOf(run) {
  return {
    id: run.id,
    file: run.file,
    status: run.status,
    createdAt: run.createdAt,
    ...(run.status === STATUS.COMPLETED ? { overall: run.overall } : {}),
  };
}

export function detailOf(run) {
  const base = {
    id: run.id,
    file: run.file,
    status: run.status,
    createdAt: run.createdAt,
  };

  if (run.status === STATUS.PROCESSING) {
    const stages = stagesFor(run);
    const totalMs = totalMsFor(run);
    const elapsed = Math.min(Date.now() - run.startedAt, totalMs - 1);
    const index = Math.floor(elapsed / STAGE_MS);
    return {
      ...base,
      stage: stages[index],
      stageIndex: index,
      stageCount: stages.length,
      progress: Math.min(0.99, elapsed / totalMs),
    };
  }

  if (run.status === STATUS.FAILED) {
    return { ...base, error: run.error };
  }

  if (run.status === STATUS.AWAITING_CDES) {
    // Enough to render "18 of 34 columns detected -- review and confirm"
    // immediately, without waiting on the separate GET .../profile fetch
    // (which supplies the full per-column table lazily, same as always).
    // No scores yet -- nothing has been evaluated.
    return {
      ...base,
      records: run.records,
      columns: totalColumnsFor(run),
      cdes: run.cdes,
    };
  }

  return {
    ...base,
    overall: run.overall,
    records: run.records,
    cdes: run.cdes,
    scores: run.scores,
    // Additive fields per API_CONTRACT.md -- a frontend that ignores them
    // behaves exactly as before notAssessed was the only one of these read.
    columns: totalColumnsFor(run),
    sampled: Boolean(run.sampled),
    sampledRows: run.sampled ? run.sampledRows : null,
    parseWarnings: parseWarningsFor(run),
    cdeOverridden: Boolean(run.cdeOverridden),
    ...(run.notAssessed ? { notAssessed: run.notAssessed } : {}),
  };
}

// Ordered so the first entries are legitimately CDE-like (identifiers, dates,
// amounts) and the tail is metadata/free-text -- runProfile below just slices
// this by count rather than randomly deciding what "looks like a CDE".
const COLUMN_POOL = [
  { name: 'customer_id', inferredType: 'string', semanticType: 'identifier' },
  { name: 'email', inferredType: 'string', semanticType: 'email' },
  { name: 'phone_number', inferredType: 'string', semanticType: 'phone' },
  { name: 'first_name', inferredType: 'string', semanticType: 'name' },
  { name: 'last_name', inferredType: 'string', semanticType: 'name' },
  { name: 'address_line_1', inferredType: 'string', semanticType: 'address' },
  { name: 'city', inferredType: 'string', semanticType: 'address' },
  { name: 'state', inferredType: 'string', semanticType: 'address' },
  { name: 'postal_code', inferredType: 'string', semanticType: 'address' },
  { name: 'country_code', inferredType: 'string', semanticType: 'reference' },
  { name: 'signup_date', inferredType: 'date', semanticType: 'date' },
  { name: 'order_id', inferredType: 'string', semanticType: 'identifier' },
  { name: 'order_date', inferredType: 'date', semanticType: 'date' },
  { name: 'unit_price', inferredType: 'number', semanticType: 'amount' },
  { name: 'quantity', inferredType: 'number', semanticType: 'amount' },
  { name: 'total_amount', inferredType: 'number', semanticType: 'amount' },
  { name: 'currency', inferredType: 'string', semanticType: 'reference' },
  { name: 'account_balance', inferredType: 'number', semanticType: 'amount' },
  { name: 'region_code', inferredType: 'string', semanticType: 'reference' },
  { name: 'sales_rep', inferredType: 'string', semanticType: 'name' },
  { name: 'created_by', inferredType: 'string', semanticType: 'metadata' },
  { name: 'updated_by', inferredType: 'string', semanticType: 'metadata' },
  { name: 'created_at', inferredType: 'date', semanticType: 'metadata' },
  { name: 'updated_at', inferredType: 'date', semanticType: 'metadata' },
  { name: 'source_system', inferredType: 'string', semanticType: 'metadata' },
  { name: 'batch_id', inferredType: 'string', semanticType: 'metadata' },
  { name: 'row_hash', inferredType: 'string', semanticType: 'metadata' },
  { name: 'notes', inferredType: 'string', semanticType: 'free_text' },
];

const CDE_REASONS = [
  'Name matches an identifier pattern; near-unique values; well populated',
  'High fill rate and a consistent format across the sampled rows',
  'Frequently referenced by other CDEs in cross-field consistency rules',
];

const NON_CDE_REASONS = [
  'Matches a metadata column pattern; single repeated value',
  'Free-text field with low signal for scoring',
  'Mostly duplicate or constant values across the sample',
];

function sampleFor(semanticType, rand, index) {
  switch (semanticType) {
    case 'identifier':
      return `ID-${10000 + Math.floor(rand() * 89999)}`;
    case 'email':
      return `user${Math.floor(rand() * 9999)}@example.com`;
    case 'phone':
      return `+1-555-${1000 + Math.floor(rand() * 8999)}`;
    case 'name':
      return ['Alex Chen', 'Jordan Lee', 'Priya Nair', 'Sam Ortiz'][index % 4];
    case 'address':
      return ['221 Baker St', '9 Market Ave', '44 Elm Rd'][index % 3];
    case 'date':
      return new Date(2024, Math.floor(rand() * 12), 1 + Math.floor(rand() * 27))
        .toISOString()
        .slice(0, 10);
    case 'amount':
      return (rand() * 500).toFixed(2);
    case 'reference':
      return ['US', 'CA', 'GB', 'AU'][index % 4];
    case 'metadata':
      return 'ETL_LOAD';
    default:
      return '(text)';
  }
}

// How many columns this run's file "has". Its own tiny rng draw (just
// `extra`), seeded off the run id -- kept separate from runProfile's
// per-column rng below so detailOf can call this alone without generating an
// entire profile just to read a count, while still landing on the exact same
// number runProfile would.
function totalColumnsFor(run) {
  const rand = rng(hash(`${run.id}:profile`));
  const cdeCount = Math.min(run.cdes, COLUMN_POOL.length);
  const extra = 4 + Math.floor(rand() * 8);
  return Math.min(COLUMN_POOL.length, cdeCount + extra);
}

// Per-column profile backing GET /runs/{id}/profile. `run.cdes` columns are
// marked isCde in pool order; a handful more are appended as the "rest of the
// file" so the panel has something non-trivial to show alongside them.
export function runProfile(run) {
  const totalColumns = totalColumnsFor(run);
  const cdeCount = Math.min(run.cdes, COLUMN_POOL.length);
  const rand = rng(hash(`${run.id}:profile:columns`));

  const columns = COLUMN_POOL.slice(0, totalColumns).map((column, i) => {
    const isCde = i < cdeCount;
    const fillRate = isCde ? 0.9 + rand() * 0.099 : 0.35 + rand() * 0.6;
    const distinctRatio = isCde ? 0.85 + rand() * 0.15 : rand() * 0.35;

    return {
      name: column.name,
      inferredType: column.inferredType,
      semanticType: column.semanticType,
      fillRate: Math.round(fillRate * 1000) / 1000,
      distinctRatio: Math.round(distinctRatio * 1000) / 1000,
      sampleValues: [0, 1, 2].map((j) => sampleFor(column.semanticType, rand, i + j)),
      isCde,
      cdeScore: Math.round((isCde ? 0.72 + rand() * 0.28 : rand() * 0.35) * 100) / 100,
      cdeReason: isCde
        ? CDE_REASONS[i % CDE_REASONS.length]
        : NON_CDE_REASONS[i % NON_CDE_REASONS.length],
    };
  });

  return { columns };
}

// PUT /runs/{id}/cdes. Re-runs only Evaluating + Scoring (see REASSESS_STAGES
// above) -- the source file, its parse/profile results, and any
// not-assessed dimension are unaffected by which columns are treated as CDEs.
export function applyCdeOverride(run, columns) {
  // Compare against the CURRENT auto-detected set before overwriting run.cdes
  // below -- cdeOverridden should be false when a user simply confirms the
  // detected defaults as-is (the common path out of awaiting_cdes), and true
  // only when the confirmed set actually differs from what was detected.
  const defaultNames = runProfile(run).columns.filter((c) => c.isCde).map((c) => c.name);
  const isDefault =
    defaultNames.length === columns.length && defaultNames.every((name) => columns.includes(name));

  run.status = STATUS.PROCESSING;
  run.processingKind = 'scoring';
  run.startedAt = Date.now();
  run.cdes = columns.length;
  run.cdeOverridden = !isDefault;
  run.overrideColumns = columns;
}

function formatMb(bytes) {
  return `${Math.round(bytes / (1024 * 1024))} MB`;
}

// The authoritative checks behind POST /runs, kept here as a plain function
// (not inline in the HTTP handler) specifically so they're testable without
// going through fetch/FormData -- see db.test.js. Returns null when the file
// is acceptable, or { status, error } naming exactly why it isn't.
export function validateUploadFile(file) {
  if (file.size > MAX_UPLOAD_BYTES) {
    return {
      status: 413,
      error: `File is ${formatMb(file.size)}. The maximum accepted size is ${formatMb(
        MAX_UPLOAD_BYTES,
      )}. Split the export into smaller files, or export a subset of columns.`,
    };
  }

  const hasAcceptedExtension = ACCEPTED_UPLOAD_EXTENSIONS.some((ext) =>
    file.name.toLowerCase().endsWith(ext),
  );
  if (!hasAcceptedExtension) {
    const extension = file.name.includes('.')
      ? file.name.slice(file.name.lastIndexOf('.'))
      : 'an unrecognised type';
    return {
      status: 415,
      error: `Only CSV files are accepted. This file appears to be ${extension} — export it as CSV and try again.`,
    };
  }

  if (file.size === 0) {
    return { status: 400, error: 'This file is empty.' };
  }

  return null;
}

// The authoritative checks behind PUT /runs/{id}/cdes, same reasoning as
// validateUploadFile above. Returns null when the override is acceptable, or
// { status, error }.
// The authoritative check behind GET /runs/{id}/profile, same reasoning as
// validateUploadFile/validateCdeOverride: a plain function so the handler and
// its tests agree on the exact rule without going through HTTP.
export function validateProfileAccess(run) {
  if (![STATUS.AWAITING_CDES, STATUS.COMPLETED].includes(run.status)) {
    return { status: 409, error: 'The column profile is not available until profiling has finished.' };
  }
  return null;
}

export function validateCdeOverride(run, columns) {
  // Valid from awaiting_cdes (the first, required confirmation) or from
  // completed (an optional later re-review) -- anything else (still
  // profiling, failed) has no CDE set to confirm yet or ever.
  if (![STATUS.AWAITING_CDES, STATUS.COMPLETED].includes(run.status)) {
    return { status: 409, error: 'This run is not awaiting CDE confirmation or completed.' };
  }

  if (!Array.isArray(columns) || columns.length === 0) {
    return { status: 400, error: 'Select at least one column.' };
  }

  const validNames = new Set(runProfile(run).columns.map((column) => column.name));
  const unknown = columns.find((name) => !validNames.has(name));
  if (unknown) {
    return { status: 400, error: `Column "${unknown}" was not found in this file.` };
  }

  return null;
}

// integrity is deliberately not listed here -- its rule ids don't follow the
// generic "{PREFIX}-{nn}" scheme (two are fixed whole-record ids, two are
// per-column ids), so it's special-cased in buildRuleStats via
// INTEGRITY_RULES below instead of this generic name list.
const RULES = {
  completeness: ['Null rate within threshold', 'Mandatory CDEs populated', 'No all-blank rows'],
  validity: ['Matches declared data type', 'Value in reference set', 'Pattern / regex conformance'],
  uniqueness: ['Primary key unique', 'No exact duplicate rows', 'Fuzzy duplicate rate'],
  consistency: ['Cross-field logic holds', 'Units consistent across rows', 'Reference integrity'],
  accuracy: ['Within expected range', 'Matches system of record', 'Outlier rate acceptable'],
  timeliness: ['Record freshness', 'Load lag within SLA', 'No future-dated records'],
};

// Per API_CONTRACT.md revision 3: two of these are whole-record checks
// (column: null on the rule itself -- the FIRST place in this file that's
// true for the majority of a dimension's rules, worth double-checking the
// drawer renders cleanly), two are per-column checks that would repeat once
// per matching column on a real file; the mock keeps one example of each so
// both shapes are exercised. Their individual violations DO carry a column
// even for the whole-record rules -- see EXAMPLE_TEMPLATES.integrity below.
const INTEGRITY_RULES = [
  { id: 'INT-CARDINALITY', name: 'Cross-field cardinality holds', column: null, severity: 'high' },
  { id: 'INT-DEPENDENT-FIELD', name: 'Dependent field completeness', column: null, severity: 'high' },
  {
    id: 'INT-POSTCODE-COUNTRY-01',
    name: 'Postcode matches country',
    column: 'postal_code',
    severity: 'medium',
  },
  { id: 'INT-ZIP-STATE-01', name: 'ZIP matches state', column: 'postal_code', severity: 'medium' },
];

// Drives the weighting inside a dimension score in the real engine -- shown
// alongside pass rate so two rules with similar pass rates can be seen
// moving the score by different amounts.
const RULE_SEVERITY = {
  completeness: ['high', 'high', 'medium'],
  validity: ['high', 'medium', 'medium'],
  uniqueness: ['high', 'medium', 'low'],
  consistency: ['medium', 'medium', 'low'],
  accuracy: ['high', 'medium', 'low'],
  timeliness: ['medium', 'low', 'low'],
};

// One or more sample violations per rule, cycled to fill out however many
// examples are requested. This is what a real engine would capture while a
// rule actually runs over the file -- the row, the offending column, the bad
// value, and why it failed.
const EXAMPLE_TEMPLATES = {
  completeness: [
    [
      { column: 'customer_id', value: '(null)', reason: 'Required field is empty' },
      { column: 'email', value: '(null)', reason: 'Required field is empty' },
      { column: 'phone_number', value: '(null)', reason: 'Required field is empty' },
    ],
    [
      { column: 'tax_id', value: '(null)', reason: 'Mandatory CDE has no value' },
      { column: 'date_of_birth', value: '(null)', reason: 'Mandatory CDE has no value' },
    ],
    [{ column: '(all fields)', value: '(blank row)', reason: 'Every field in this record is blank' }],
  ],
  validity: [
    [
      { column: 'order_date', value: '13/45/2026', reason: 'Not a valid date' },
      { column: 'unit_price', value: 'N/A', reason: 'Expected a number' },
    ],
    [
      { column: 'country_code', value: 'XX', reason: 'Not in the ISO country list' },
      { column: 'currency', value: 'USD$', reason: 'Not a recognised currency code' },
    ],
    [
      { column: 'email', value: 'j.doe@@mail', reason: 'Fails the email pattern' },
      { column: 'postal_code', value: '9A9A9', reason: 'Does not match the expected format' },
    ],
  ],
  uniqueness: [
    [{ column: 'customer_id', value: '100482', reason: 'Duplicate primary key' }],
    [{ column: '(all fields)', value: '(duplicate)', reason: 'Identical to another row in the file' }],
    [{ column: 'company_name', value: 'Acme Corp.', reason: 'Near-duplicate of another row (95% similar)' }],
  ],
  consistency: [
    [{ column: 'ship_date', value: 'before order_date', reason: 'Shipped before it was ordered' }],
    [{ column: 'weight_unit', value: 'lb', reason: 'Rest of the file uses kg' }],
    [{ column: 'region_code', value: 'EMEA-9', reason: 'No matching region in the reference table' }],
  ],
  accuracy: [
    [{ column: 'unit_price', value: '-14.50', reason: 'Negative price' }],
    [{ column: 'account_balance', value: '1,204.00', reason: 'Differs from the source system (1,240.00)' }],
    [{ column: 'order_quantity', value: '48,000', reason: 'Statistical outlier (>6σ from the mean)' }],
  ],
  timeliness: [
    [{ column: 'last_updated', value: '2019-03-01', reason: 'Not updated in over 5 years' }],
    [{ column: 'ingested_at', value: '(delayed 4d)', reason: 'Loaded 4 days after its source timestamp' }],
    [{ column: 'created_at', value: '2027-01-01', reason: 'Timestamp is in the future' }],
  ],
  // Note the two whole-record rules (index 0, 1) still name a real column per
  // violation -- INT-CARDINALITY/INT-DEPENDENT-FIELD have column: null on the
  // *rule*, but each individual example is about a specific field. That's the
  // "the drawer's rule row has no column while its examples do" distinction
  // from API_CONTRACT.md, not an inconsistency.
  integrity: [
    [
      {
        column: 'product_code',
        value: 'SKU-2291 → two different names',
        reason: 'Same code resolves to two different product names elsewhere in the file',
      },
    ],
    [
      {
        column: 'cancellation_reason',
        value: '(null)',
        reason: 'Populated on every other cancelled row, empty on this one',
      },
    ],
    [
      {
        column: 'postal_code',
        value: '90210 with country GB',
        reason: 'Postcode format implies US, but country is GB',
      },
    ],
    [
      {
        column: 'postal_code',
        value: '10001 with state CA',
        reason: 'ZIP implies NY, but state is CA',
      },
    ],
  ],
};

function ruleColumnFor(key, index) {
  const column = EXAMPLE_TEMPLATES[key]?.[index]?.[0]?.column;
  return column && column !== '(all fields)' ? column : null;
}

export const MAX_RULE_EXAMPLES = 10;

// Shared by dimensionDetail and ruleExamples, so a rule's pass rate is
// computed exactly once and both endpoints always agree on it.
function buildRuleStats(run, key) {
  const score = run.scores?.[key];
  // Both "key doesn't exist" (undefined) and "not assessed" (null) mean there
  // are no rules to report -- dimensionDetail handles the not-assessed case
  // explicitly before ever reaching here, but this stays a safe fallback.
  if (score === undefined || score === null) return null;

  const rand = rng(hash(run.id + key));

  if (key === 'integrity') {
    return INTEGRITY_RULES.map((rule) => {
      const passRate = Math.min(1, Math.max(0, score / 100 + (rand() * 0.12 - 0.06)));
      return {
        id: rule.id,
        name: rule.name,
        passRate,
        column: rule.column, // null for the two whole-record rules, on purpose
        severity: rule.severity,
        evaluated: run.records,
        failed: Math.round((1 - passRate) * run.records),
      };
    });
  }

  const names = RULES[key] ?? [];
  return names.map((name, i) => {
    const passRate = Math.min(1, Math.max(0, score / 100 + (rand() * 0.12 - 0.06)));
    return {
      id: `${key.slice(0, 3).toUpperCase()}-${String(i + 1).padStart(2, '0')}`,
      name,
      passRate,
      // Additive per-rule fields per API_CONTRACT.md.
      column: ruleColumnFor(key, i),
      severity: RULE_SEVERITY[key]?.[i] ?? 'medium',
      evaluated: run.records,
      failed: Math.round((1 - passRate) * run.records),
    };
  });
}

export function dimensionDetail(run, key) {
  if (run.notAssessed?.[key]) {
    return { key, score: null, rules: [], notAssessedReason: run.notAssessed[key] };
  }

  const rules = buildRuleStats(run, key);
  if (!rules) return null;
  return { key, score: run.scores[key], rules };
}

// Up to `limit` concrete example rows that failed a single rule, plus the
// total failing count so the UI can say "10 of 5,136".
export function ruleExamples(run, key, ruleId, limit = MAX_RULE_EXAMPLES) {
  const rules = buildRuleStats(run, key);
  const index = rules?.findIndex((rule) => rule.id === ruleId) ?? -1;
  if (!rules || index === -1) return null;

  const rule = rules[index];
  const total = Math.max(1, Math.round((1 - rule.passRate) * run.records));
  const count = Math.min(limit, MAX_RULE_EXAMPLES, total);

  const templates = EXAMPLE_TEMPLATES[key]?.[index] ?? [
    { column: '(value)', value: '(invalid)', reason: 'Fails this rule' },
  ];

  // Distinct, ascending row numbers so the table reads like a real scan of the file.
  const rand = rng(hash(`${run.id}:${ruleId}`));
  const rows = new Set();
  while (rows.size < count) {
    rows.add(1 + Math.floor(rand() * run.records));
  }

  const examples = [...rows].sort((a, b) => a - b).map((row, i) => ({
    row,
    ...templates[i % templates.length],
  }));

  return { ruleId, ruleName: rule.name, passRate: rule.passRate, total, examples };
}
