// Shared vocabulary. Because there are no types in this project, these frozen
// objects are what keep status strings and dimension keys from drifting between
// the API client, the components, and the mock handlers (which import them too).

export const STATUS = Object.freeze({
  PROCESSING: 'processing',
  // Sits between the Profiling and Evaluating stages. The run pauses here
  // with its auto-detected CDEs for the user to review/edit; nothing
  // advances until PUT /runs/{id}/cdes confirms a selection. Deliberately a
  // distinct status rather than a flag on 'processing' -- every existing
  // poll condition and status switch in this app already treats status as a
  // closed set of exact values, so this gets correct "don't poll, don't
  // auto-advance" behaviour for free, with no extra checks anywhere.
  AWAITING_CDES: 'awaiting_cdes',
  COMPLETED: 'completed',
  FAILED: 'failed',
});

export const DIMENSIONS = Object.freeze([
  { key: 'completeness', label: 'Completeness' },
  { key: 'validity', label: 'Validity' },
  { key: 'uniqueness', label: 'Uniqueness' },
  { key: 'consistency', label: 'Consistency' },
  { key: 'accuracy', label: 'Accuracy' },
  { key: 'timeliness', label: 'Timeliness' },
  // Added in API_CONTRACT.md revision 3. Deliberately just "Integrity" --
  // not "Referential integrity" -- it only checks relationships within this
  // one file (cardinality, dependent fields, postcode/ZIP self-consistency),
  // not foreign keys against other systems. See DimensionDrawer.jsx and the
  // revision 3 notes for what it does and doesn't cover; the label must not
  // overclaim what the rule names underneath it actually checked.
  { key: 'integrity', label: 'Integrity' },
]);

export const DIMENSION_KEYS = Object.freeze(DIMENSIONS.map((d) => d.key));

export function dimensionLabel(key) {
  return DIMENSIONS.find((d) => d.key === key)?.label ?? key;
}

// Ordered stages a processing run moves through, per API_CONTRACT.md's
// backend note on GET /runs/{id}.
export const STAGES = Object.freeze(['Ingesting', 'Profiling', 'Evaluating', 'Scoring']);

// Upload limits. Raised from FR-1.1's original 200 MB / .csv-only to match
// API_CONTRACT.md's backend proposal -- these constants are what
// validateCsv.js and the mock's server-side check both read, so the two
// can't drift apart. The mock treats these as authoritative and rejects with
// 413/415/400 accordingly; the client-side check is intentionally a light,
// non-blocking sanity pass, not a duplicate of backend validation.
export const MAX_UPLOAD_BYTES = 500 * 1024 * 1024;
export const ACCEPTED_UPLOAD_EXTENSIONS = Object.freeze(['.csv', '.tsv', '.txt']);
