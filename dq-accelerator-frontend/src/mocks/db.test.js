import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { allRuns, createRun, detailOf, dimensionDetail, findRun, summaryOf } from './db';
import { STATUS, DIMENSION_KEYS } from '@/api/constants';

const PROFILE_MS = 3000 * 2; // Ingesting + Profiling
const SCORING_MS = 3000 * 2; // Evaluating + Scoring

// Drives a run through BOTH phases of the lifecycle: Ingesting + Profiling to
// awaiting_cdes, then a confirmation (defaulting to whatever was
// auto-detected) through Evaluating + Scoring to completed. This replaces
// the old single "advance 4 stages" helper now that a human confirmation
// sits between the two halves -- see db.js's resolve().
async function completeRun(filename, { columns } = {}) {
  const { applyCdeOverride, runProfile } = await import('./db');

  vi.useFakeTimers();
  const created = createRun(filename);
  vi.advanceTimersByTime(PROFILE_MS + 1);
  const awaiting = findRun(created.id);

  if (awaiting.status === STATUS.FAILED) {
    vi.useRealTimers();
    return awaiting; // a willFail run never reaches awaiting_cdes
  }

  const confirmColumns =
    columns ?? runProfile(awaiting).columns.filter((c) => c.isCde).map((c) => c.name);
  applyCdeOverride(awaiting, confirmColumns);
  vi.advanceTimersByTime(SCORING_MS + 1);
  const run = findRun(created.id);
  vi.useRealTimers();
  return run;
}

describe('mock run lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('seeds a completed, a second completed, and a failed run', () => {
    const statuses = allRuns().map((r) => r.status);
    expect(statuses).toContain(STATUS.COMPLETED);
    expect(statuses).toContain(STATUS.FAILED);
  });

  it('pauses at awaiting_cdes after Ingesting + Profiling, with no scores yet', () => {
    const created = createRun('orders.csv');
    expect(created.status).toBe(STATUS.PROCESSING);

    const stages = [];
    for (let elapsed = 0; elapsed < PROFILE_MS; elapsed += 3000) {
      const detail = detailOf(findRun(created.id));
      stages.push(detail.stage);
      expect(detail.progress).toBeLessThan(1);
      vi.advanceTimersByTime(3000);
    }
    expect(stages).toEqual(['Ingesting', 'Profiling']);

    const awaiting = detailOf(findRun(created.id));
    expect(awaiting.status).toBe(STATUS.AWAITING_CDES);
    expect(typeof awaiting.cdes).toBe('number');
    expect(typeof awaiting.columns).toBe('number');
    expect(awaiting.scores).toBeUndefined();
    expect(awaiting.overall).toBeUndefined();
  });

  it('only advances to Evaluating + Scoring once the CDE set is confirmed', async () => {
    const { applyCdeOverride, runProfile } = await import('./db');
    const created = createRun('confirm-me.csv');
    vi.advanceTimersByTime(PROFILE_MS + 1);
    const awaiting = findRun(created.id);
    expect(awaiting.status).toBe(STATUS.AWAITING_CDES);

    // Sitting at awaiting_cdes is a resting state -- more elapsed time alone
    // must not advance it.
    vi.advanceTimersByTime(3000 * 10);
    expect(findRun(created.id).status).toBe(STATUS.AWAITING_CDES);

    const columns = runProfile(awaiting).columns.filter((c) => c.isCde).map((c) => c.name);
    applyCdeOverride(awaiting, columns);

    const stages = [];
    for (let elapsed = 0; elapsed < SCORING_MS; elapsed += 3000) {
      stages.push(detailOf(findRun(created.id)).stage);
      vi.advanceTimersByTime(3000);
    }
    expect(stages).toEqual(['Evaluating', 'Scoring']);

    const done = detailOf(findRun(created.id));
    expect(done.status).toBe(STATUS.COMPLETED);
    expect(Object.keys(done.scores).sort()).toEqual([...DIMENSION_KEYS].sort());
    expect(done.overall).toBeGreaterThan(0);
  });

  it('keeps a completed run scores stable across polls', async () => {
    const run = await completeRun('stable.csv');
    const first = detailOf(findRun(run.id));
    const second = detailOf(findRun(run.id));
    expect(second.scores).toEqual(first.scores);
    expect(second.overall).toBe(first.overall);
  });

  it('fails deterministically for a filename flagged as bad, without ever reaching awaiting_cdes', async () => {
    const run = await completeRun('broken-fail.csv');
    const detail = detailOf(findRun(run.id));
    expect(detail.status).toBe(STATUS.FAILED);
    expect(detail.error).toBeTruthy();
    expect(detail.scores).toBeUndefined();
  });

  it('omits overall from summaries until a run completes -- including while awaiting_cdes', () => {
    const created = createRun('pending.csv');
    expect(summaryOf(findRun(created.id)).overall).toBeUndefined();

    vi.advanceTimersByTime(PROFILE_MS + 1);
    expect(findRun(created.id).status).toBe(STATUS.AWAITING_CDES);
    expect(summaryOf(findRun(created.id)).overall).toBeUndefined();
  });

  it('serves rule detail that agrees with the tile score', async () => {
    const run = await completeRun('rules.csv');
    const detail = dimensionDetail(run, 'completeness');
    expect(detail.score).toBe(run.scores.completeness);
    expect(detail.rules).toHaveLength(3);
    detail.rules.forEach((rule) => {
      expect(rule.passRate).toBeGreaterThanOrEqual(0);
      expect(rule.passRate).toBeLessThanOrEqual(1);
      expect(rule.id).toMatch(/^COM-\d{2}$/);
    });
  });
});

describe('deleting runs', () => {
  it('removes a run and reports whether it existed', async () => {
    const { createRun: create, removeRun, findRun: find, allRuns: all } = await import('./db');
    const created = create('to-delete.csv');
    expect(find(created.id)).toBeTruthy();

    expect(removeRun(created.id)).toBe(true);
    expect(find(created.id)).toBeNull();
    expect(all().some((r) => r.id === created.id)).toBe(false);

    expect(removeRun(created.id)).toBe(false);
  });
});

describe('rule examples', () => {
  it('returns up to 10 distinct, ascending rows with a matching total', async () => {
    const { ruleExamples } = await import('./db');
    const run = await completeRun('examples.csv');
    const result = ruleExamples(run, 'completeness', 'COM-01');

    expect(result.total).toBeGreaterThan(0);
    expect(result.examples.length).toBeLessThanOrEqual(10);
    expect(result.examples.length).toBeLessThanOrEqual(result.total);

    const rows = result.examples.map((e) => e.row);
    expect(new Set(rows).size).toBe(rows.length); // no duplicate rows
    expect(rows).toEqual([...rows].sort((a, b) => a - b)); // ascending

    result.examples.forEach((example) => {
      expect(example.column).toBeTruthy();
      expect(example.value).toBeTruthy();
      expect(example.reason).toBeTruthy();
      expect(example.row).toBeGreaterThanOrEqual(1);
      expect(example.row).toBeLessThanOrEqual(run.records);
    });
  });

  it('agrees with dimensionDetail on the pass rate for the same rule', async () => {
    const { ruleExamples } = await import('./db');
    const run = await completeRun('agree.csv');
    const dim = dimensionDetail(run, 'validity');
    const rule = dim.rules[1]; // VAL-02
    const result = ruleExamples(run, 'validity', rule.id);

    expect(result.passRate).toBe(rule.passRate);
  });

  it('never asks for more examples than actually exist', async () => {
    const { ruleExamples } = await import('./db');
    const run = await completeRun('capped.csv');
    run.records = 3; // force total failing count below the 10-example cap
    const result = ruleExamples(run, 'timeliness', 'TIM-01');

    expect(result.examples.length).toBeLessThanOrEqual(result.total);
    expect(result.examples.length).toBeLessThanOrEqual(3);
  });

  it('returns null for a rule id that does not exist on the dimension', async () => {
    const { ruleExamples } = await import('./db');
    const run = await completeRun('missing-rule.csv');
    expect(ruleExamples(run, 'completeness', 'COM-99')).toBeNull();
  });
});

describe('not-assessed dimensions', () => {
  it('reports timeliness as null with a reason for a "nodate" filename', async () => {
    const run = await completeRun('nodate-export.csv');

    expect(run.scores.timeliness).toBeNull();
    expect(run.notAssessed).toEqual({
      timeliness: 'No date column was detected in this file.',
    });
    // Every other dimension is still a real number.
    expect(typeof run.scores.completeness).toBe('number');
  });

  it('excludes the not-assessed dimension from the overall average', async () => {
    const run = await completeRun('no-timeliness-sample.csv');
    const scored = Object.values(run.scores).filter((v) => typeof v === 'number');
    const expected = Math.round((scored.reduce((a, b) => a + b, 0) / scored.length) * 10) / 10;

    expect(run.overall).toBe(expected);
    expect(Number.isNaN(run.overall)).toBe(false);
  });

  it('dimensionDetail returns score null, no rules, and a reason', async () => {
    const run = await completeRun('nodate-rules.csv');

    const detail = dimensionDetail(run, 'timeliness');
    expect(detail).toEqual({
      key: 'timeliness',
      score: null,
      rules: [],
      notAssessedReason: 'No date column was detected in this file.',
    });
  });

  it('ruleExamples 404s (returns null) for any rule under a not-assessed dimension', async () => {
    const { ruleExamples } = await import('./db');
    const run = await completeRun('nodate-examples.csv');
    expect(ruleExamples(run, 'timeliness', 'TIM-01')).toBeNull();
  });

  it('a normal filename is unaffected', async () => {
    const run = await completeRun('regular-file.csv');
    expect(run.notAssessed).toBeUndefined();
    expect(Object.values(run.scores).every((v) => typeof v === 'number')).toBe(true);
  });
});

describe('runProfile', () => {
  it('marks exactly run.cdes columns as CDEs, in pool order', async () => {
    const { runProfile } = await import('./db');
    const run = await completeRun('profile-sample.csv');

    const profile = runProfile(run);
    const cdeColumns = profile.columns.filter((c) => c.isCde);

    expect(cdeColumns).toHaveLength(Math.min(run.cdes, profile.columns.length));
    // The CDE columns are a contiguous prefix -- no non-CDE column sits before one.
    const firstNonCde = profile.columns.findIndex((c) => !c.isCde);
    if (firstNonCde !== -1) {
      expect(profile.columns.slice(0, firstNonCde).every((c) => c.isCde)).toBe(true);
    }
  });

  it('gives every column plausible sample values and a reason', async () => {
    const { runProfile } = await import('./db');
    const run = await completeRun('profile-values.csv');

    const profile = runProfile(run);
    profile.columns.forEach((column) => {
      expect(column.sampleValues).toHaveLength(3);
      expect(column.cdeReason).toBeTruthy();
      expect(column.fillRate).toBeGreaterThanOrEqual(0);
      expect(column.fillRate).toBeLessThanOrEqual(1);
      expect(column.distinctRatio).toBeGreaterThanOrEqual(0);
      expect(column.distinctRatio).toBeLessThanOrEqual(1);
    });
  });

  it('is deterministic for the same run', async () => {
    const { runProfile } = await import('./db');
    const run = await completeRun('profile-stable.csv');

    expect(runProfile(run)).toEqual(runProfile(run));
  });

  it('is already available while a run is awaiting_cdes, not just once completed', async () => {
    const { runProfile } = await import('./db');
    vi.useFakeTimers();
    const created = createRun('profile-early.csv');
    vi.advanceTimersByTime(PROFILE_MS + 1);
    const awaiting = findRun(created.id);
    vi.useRealTimers();

    expect(awaiting.status).toBe(STATUS.AWAITING_CDES);
    expect(runProfile(awaiting).columns.length).toBeGreaterThan(0);
  });
});

describe('additive completed-run fields', () => {
  it('exposes columns, sampled, parseWarnings, and cdeOverridden on a normal run', async () => {
    const run = await completeRun('additive-fields.csv');
    const detail = detailOf(run);

    expect(typeof detail.columns).toBe('number');
    expect(detail.columns).toBeGreaterThanOrEqual(run.cdes);
    expect(detail.sampled).toBe(false);
    expect(detail.sampledRows).toBeNull();
    expect(typeof detail.parseWarnings).toBe('number');
    expect(detail.cdeOverridden).toBe(false);
  });

  it('flags a "sampled" filename with sampledRows below the full record count', async () => {
    const run = await completeRun('sampled-export.csv');
    const detail = detailOf(run);

    expect(detail.sampled).toBe(true);
    expect(detail.sampledRows).toBeLessThan(detail.records);
  });
});

describe('CDE confirmation and override (PUT /runs/{id}/cdes)', () => {
  it('confirming the auto-detected defaults out of awaiting_cdes leaves cdeOverridden false', async () => {
    const run = await completeRun('confirm-defaults.csv'); // completeRun confirms the detected defaults as-is
    expect(detailOf(run).cdeOverridden).toBe(false);
  });

  it('confirming a different set than detected sets cdeOverridden true from the start', async () => {
    const { runProfile } = await import('./db');
    vi.useFakeTimers();
    const created = createRun('confirm-edited.csv');
    vi.advanceTimersByTime(PROFILE_MS + 1);
    const awaiting = findRun(created.id);
    vi.useRealTimers();

    const detectedCount = awaiting.cdes;
    const edited = runProfile(awaiting).columns.slice(0, 2).map((c) => c.name);
    expect(edited.length).not.toBe(detectedCount); // guaranteed distinct from the default

    const { applyCdeOverride } = await import('./db');
    vi.useFakeTimers();
    applyCdeOverride(awaiting, edited);
    vi.advanceTimersByTime(SCORING_MS + 1);
    const run = findRun(created.id);
    vi.useRealTimers();

    expect(run.cdeOverridden).toBe(true);
    expect(run.cdes).toBe(edited.length);
  });

  it('re-runs only Evaluating and Scoring on a later re-review of a completed run', async () => {
    const { applyCdeOverride, runProfile } = await import('./db');
    const run = await completeRun('override-sample.csv');
    const newColumns = runProfile(run).columns.slice(0, 3).map((c) => c.name);

    vi.useFakeTimers();
    applyCdeOverride(run, newColumns);

    let detail = detailOf(findRun(run.id));
    expect(detail.status).toBe(STATUS.PROCESSING);
    expect(detail.stage).toBe('Evaluating');
    expect(detail.stageCount).toBe(2);

    vi.advanceTimersByTime(3000);
    detail = detailOf(findRun(run.id));
    expect(detail.stage).toBe('Scoring');

    vi.advanceTimersByTime(3000 + 1);
    detail = detailOf(findRun(run.id));
    vi.useRealTimers();

    expect(detail.status).toBe(STATUS.COMPLETED);
    expect(detail.cdeOverridden).toBe(true);
    expect(detail.cdes).toBe(newColumns.length);
    expect(Object.keys(detail.scores).length).toBe(7);
  });

  it('a not-assessed dimension survives a re-review untouched', async () => {
    const { applyCdeOverride, findRun: find, runProfile } = await import('./db');
    const run = await completeRun('nodate-override.csv');
    expect(run.notAssessed).toEqual({ timeliness: 'No date column was detected in this file.' });

    const columns = runProfile(run).columns.slice(0, 4).map((c) => c.name);
    vi.useFakeTimers();
    applyCdeOverride(run, columns);
    vi.advanceTimersByTime(SCORING_MS + 1);
    const reassessed = find(run.id);
    vi.useRealTimers();

    expect(reassessed.scores.timeliness).toBeNull();
    expect(reassessed.notAssessed).toEqual({
      timeliness: 'No date column was detected in this file.',
    });
  });
});

describe('validateUploadFile', () => {
  function fakeFile(name, size) {
    return { name, size };
  }

  it('accepts a normal csv', async () => {
    const { validateUploadFile } = await import('./db');
    expect(validateUploadFile(fakeFile('customers.csv', 2048))).toBeNull();
  });

  it('rejects a file over 500 MB with 413', async () => {
    const { validateUploadFile } = await import('./db');
    const result = validateUploadFile(fakeFile('big.csv', 500 * 1024 * 1024 + 1));
    expect(result.status).toBe(413);
    expect(result.error).toMatch(/maximum accepted size/i);
  });

  it('rejects an unsupported extension with 415', async () => {
    const { validateUploadFile } = await import('./db');
    const result = validateUploadFile(fakeFile('report.xlsx', 100));
    expect(result.status).toBe(415);
    expect(result.error).toMatch(/only csv files are accepted/i);
  });

  it('rejects an empty file with 400', async () => {
    const { validateUploadFile } = await import('./db');
    const result = validateUploadFile(fakeFile('empty.csv', 0));
    expect(result.status).toBe(400);
    expect(result.error).toMatch(/empty/i);
  });

  it('accepts the extensions the backend proposal added', async () => {
    const { validateUploadFile } = await import('./db');
    expect(validateUploadFile(fakeFile('export.tsv', 100))).toBeNull();
    expect(validateUploadFile(fakeFile('export.txt', 100))).toBeNull();
  });
});

describe('validateProfileAccess', () => {
  it('rejects a run still Ingesting/Profiling with 409', async () => {
    const { validateProfileAccess } = await import('./db');
    vi.useFakeTimers();
    const created = createRun('validate-profile-early.csv');
    const run = findRun(created.id); // no time advanced -- still processing
    vi.useRealTimers();

    expect(validateProfileAccess(run).status).toBe(409);
  });

  it('accepts a run that is awaiting_cdes', async () => {
    const { validateProfileAccess } = await import('./db');
    vi.useFakeTimers();
    const created = createRun('validate-profile-awaiting.csv');
    vi.advanceTimersByTime(PROFILE_MS + 1);
    const run = findRun(created.id);
    vi.useRealTimers();

    expect(run.status).toBe(STATUS.AWAITING_CDES);
    expect(validateProfileAccess(run)).toBeNull();
  });

  it('accepts a completed run', async () => {
    const { validateProfileAccess } = await import('./db');
    const run = await completeRun('validate-profile-completed.csv');
    expect(validateProfileAccess(run)).toBeNull();
  });
});

describe('validateCdeOverride', () => {
  it('accepts a real subset of the file’s columns on a completed run', async () => {
    const { validateCdeOverride, runProfile } = await import('./db');
    const run = await completeRun('validate-override.csv');
    const columns = runProfile(run).columns.slice(0, 2).map((c) => c.name);
    expect(validateCdeOverride(run, columns)).toBeNull();
  });

  it('accepts a selection while awaiting_cdes -- this is the required first confirmation', async () => {
    const { validateCdeOverride, runProfile } = await import('./db');
    vi.useFakeTimers();
    const created = createRun('validate-override-awaiting.csv');
    vi.advanceTimersByTime(PROFILE_MS + 1);
    const run = findRun(created.id);
    vi.useRealTimers();

    const columns = runProfile(run).columns.slice(0, 2).map((c) => c.name);
    expect(validateCdeOverride(run, columns)).toBeNull();
  });

  it('rejects an empty selection with 400', async () => {
    const { validateCdeOverride } = await import('./db');
    const run = await completeRun('validate-empty.csv');
    const result = validateCdeOverride(run, []);
    expect(result.status).toBe(400);
  });

  it('rejects an unknown column name with 400, naming the offender', async () => {
    const { validateCdeOverride } = await import('./db');
    const run = await completeRun('validate-unknown.csv');
    const result = validateCdeOverride(run, ['not_a_real_column']);
    expect(result.status).toBe(400);
    expect(result.error).toMatch(/not_a_real_column/);
  });

  it('rejects a run that is still Ingesting/Profiling with 409', async () => {
    const { validateCdeOverride } = await import('./db');
    vi.useFakeTimers();
    const created = createRun('validate-processing.csv');
    const run = findRun(created.id); // no time advanced -- still processing
    vi.useRealTimers();

    const result = validateCdeOverride(run, ['customer_id']);
    expect(result.status).toBe(409);
  });
});

describe('integrity dimension (API_CONTRACT.md revision 3)', () => {
  it('is scored on an ordinary run, as the 7th dimension', async () => {
    const run = await completeRun('integrity-scored.csv');
    expect(typeof run.scores.integrity).toBe('number');
    expect(Object.keys(run.scores)).toHaveLength(7);
  });

  it('uses the documented fixed rule ids, not the generic {PREFIX}-{nn} scheme', async () => {
    const run = await completeRun('integrity-rules.csv');
    const detail = dimensionDetail(run, 'integrity');

    expect(detail.rules.map((r) => r.id)).toEqual([
      'INT-CARDINALITY',
      'INT-DEPENDENT-FIELD',
      'INT-POSTCODE-COUNTRY-01',
      'INT-ZIP-STATE-01',
    ]);
  });

  it('the two whole-record rules carry column: null on the rule itself', async () => {
    const run = await completeRun('integrity-column-null.csv');
    const detail = dimensionDetail(run, 'integrity');

    expect(detail.rules[0].column).toBeNull(); // INT-CARDINALITY
    expect(detail.rules[1].column).toBeNull(); // INT-DEPENDENT-FIELD
    expect(detail.rules[2].column).toBe('postal_code'); // INT-POSTCODE-COUNTRY-01
    expect(detail.rules[3].column).toBe('postal_code'); // INT-ZIP-STATE-01
  });

  it('a whole-record rule’s own EXAMPLES still carry a real column (not null)', async () => {
    const { ruleExamples } = await import('./db');
    const run = await completeRun('integrity-example-column.csv');
    const result = ruleExamples(run, 'integrity', 'INT-CARDINALITY');

    // The rule row has no column, but its violations are about a specific
    // field -- that's intentional per API_CONTRACT.md, not a bug.
    expect(result.examples.length).toBeGreaterThan(0);
    result.examples.forEach((example) => {
      expect(example.column).toBeTruthy();
      expect(example.column).not.toBe('(all fields)');
    });
  });

  it('a "narrow" filename makes integrity (and only integrity) not-assessed', async () => {
    const run = await completeRun('narrow-export.csv');
    expect(run.scores.integrity).toBeNull();
    expect(run.scores.timeliness).toEqual(expect.any(Number));
    expect(run.notAssessed).toEqual({
      integrity:
        'Integrity compares fields against one another, and fewer than two populated critical data elements were found in this file.',
    });
  });

  it('a filename can make BOTH timeliness and integrity not-assessed at once', async () => {
    const run = await completeRun('nodate-narrow-export.csv');
    expect(run.scores.timeliness).toBeNull();
    expect(run.scores.integrity).toBeNull();
    expect(Object.keys(run.notAssessed).sort()).toEqual(['integrity', 'timeliness']);
    // The other five dimensions are unaffected.
    expect(Object.values(run.scores).filter((v) => typeof v === 'number')).toHaveLength(5);
  });
});
