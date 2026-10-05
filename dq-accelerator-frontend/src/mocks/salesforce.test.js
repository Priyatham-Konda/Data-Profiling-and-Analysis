import { afterEach, describe, expect, it, vi } from 'vitest';
import { STATUS, CONNECTION_STATE } from '@/api/constants';
import { allRuns, findRun, runDataCsv, runDataFilename, runProfile, validateCdeOverride, validateDataAccess } from './db';
import {
  assessmentDataFiles,
  assessmentDetail,
  confirmAssessmentCdes,
  createAssessment,
  createConnection,
  findAssessment,
  findConnection,
  overallScores,
  parseInstanceUrl,
  statusOf,
  validateConnectionRequest,
} from './salesforce';

const PROFILE_MS = 3000 * 2 + 1;
const SCORING_MS = 3000 * 2 + 1;

const GOOD = { instanceUrl: 'acme.my.salesforce.com', clientId: 'abc', clientSecret: 's3cret' };

function detectedCdes(run) {
  return runProfile(run).columns.filter((c) => c.isCde).map((c) => c.name);
}

describe('parseInstanceUrl', () => {
  it('accepts production and sandbox, My Domain and Lightning, with or without https', () => {
    expect(parseInstanceUrl('https://acme.my.salesforce.com/lightning/page')).toMatchObject({
      environment: 'production',
      instanceUrl: 'https://acme.my.salesforce.com',
    });
    expect(parseInstanceUrl('acme.lightning.force.com').instanceUrl).toBe(
      'https://acme.my.salesforce.com',
    );
    expect(parseInstanceUrl('acme--uat.sandbox.my.salesforce.com')).toMatchObject({
      environment: 'sandbox',
      instanceUrl: 'https://acme--uat.sandbox.my.salesforce.com',
    });
  });

  it('rejects anything that is not a Salesforce domain', () => {
    expect(parseInstanceUrl('acme.example.com')).toBeNull();
    expect(parseInstanceUrl('my.salesforce.com.evil.io')).toBeNull();
    expect(parseInstanceUrl('')).toBeNull();
  });
});

describe('validateConnectionRequest', () => {
  it('maps the demo triggers to the contract error codes', () => {
    const codeFor = (overrides) => validateConnectionRequest({ ...GOOD, ...overrides })?.errorCode;
    expect(codeFor({ instanceUrl: 'acme.example.com' })).toBe('invalid_url');
    expect(codeFor({ instanceUrl: 'unreachable.my.salesforce.com' })).toBe('unreachable');
    expect(codeFor({ clientSecret: 'invalid' })).toBe('invalid_client');
    expect(codeFor({ clientId: 'noflow' })).toBe('flow_not_enabled');
    expect(codeFor({ clientId: 'norunas' })).toBe('no_run_as_user');
    expect(codeFor({ clientId: 'noapi' })).toBe('api_disabled');
    expect(codeFor({ clientId: 'refused' })).toBe('failed');
    expect(validateConnectionRequest(GOOD)).toBeNull();
  });

  it('requires all three fields', () => {
    expect(validateConnectionRequest({ ...GOOD, clientSecret: ' ' }).status).toBe(400);
  });

  it('never keeps the secret on the connection', () => {
    const { connection } = createConnection(GOOD);
    expect(JSON.stringify(connection)).not.toContain('s3cret');
    expect(connection.state).toBe(CONNECTION_STATE.CONNECTED);
  });
});

describe('statusOf', () => {
  it('follows the precedence processing > awaiting > completed > failed', () => {
    const { PROCESSING, AWAITING_CDES, COMPLETED, FAILED } = STATUS;
    expect(statusOf([COMPLETED, PROCESSING, AWAITING_CDES])).toBe(PROCESSING);
    expect(statusOf([COMPLETED, AWAITING_CDES, FAILED])).toBe(AWAITING_CDES);
    expect(statusOf([FAILED, COMPLETED])).toBe(COMPLETED);
    expect(statusOf([FAILED, FAILED])).toBe(FAILED);
  });
});

describe('overallScores', () => {
  it('weighs each object by records, and leaves a dimension nobody assessed unscored', () => {
    const { scores, notAssessed } = overallScores([
      { records: 3000, scores: { completeness: 90, timeliness: null } },
      { records: 1000, scores: { completeness: 50, timeliness: null } },
    ]);
    expect(scores.completeness).toBe(80);
    expect(scores.timeliness).toBeNull();
    expect(notAssessed.timeliness).toBeTruthy();
  });
});

describe('a multi-object assessment', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('pauses for CDEs, rejects per-run confirmation, and completes partial when an object fails', () => {
    vi.useFakeTimers();
    const { connection } = createConnection(GOOD);
    const { result } = createAssessment({
      source: { type: 'salesforce', connectionId: connection.id },
      objects: ['Account', 'Invoice__c'],
    });
    expect(result.status).toBe(STATUS.PROCESSING);
    const assessment = findAssessment(result.id);

    // Object runs stay out of the plain run list.
    const listed = allRuns().map((r) => r.id);
    result.runs.forEach((run) => expect(listed).not.toContain(run.id));
    expect(allRuns({ includeObjectRuns: true }).map((r) => r.id)).toContain(result.runs[0].id);

    vi.advanceTimersByTime(PROFILE_MS);
    let detail = assessmentDetail(assessment);
    expect(detail.status).toBe(STATUS.AWAITING_CDES);
    // Downloads are done, so the connection closed itself.
    expect(findConnection(connection.id).closedReason).toBe('extracted');

    const account = findRun(result.runs.find((r) => r.object === 'Account').id);
    const invoice = detail.runs.find((r) => r.object === 'Invoice__c');
    expect(invoice.status).toBe(STATUS.FAILED);
    expect(invoice.error).toMatch(/Invoice/);

    // Confirming one object run on its own is refused...
    expect(validateCdeOverride(account, detectedCdes(account)).status).toBe(409);
    // ...and so is an assessment confirmation that leaves out an awaiting object.
    expect(confirmAssessmentCdes(assessment, { objects: {} }).rejection.status).toBe(400);
    expect(
      confirmAssessmentCdes(assessment, { objects: { Account: ['Not_A_Field__c'] } }).rejection
        .status,
    ).toBe(400);

    const ok = confirmAssessmentCdes(assessment, { objects: { Account: detectedCdes(account) } });
    expect(ok.result.status).toBe(STATUS.PROCESSING);

    vi.advanceTimersByTime(SCORING_MS);
    detail = assessmentDetail(assessment);
    expect(detail.status).toBe(STATUS.COMPLETED);
    expect(detail.partial).toBe(true);
    expect(detail.objects).toBe(2);
    expect(detail.records).toBe(findRun(account.id).records);
    expect(typeof detail.overall).toBe('number');
  });

  it('rejects a second assessment on the same connection', () => {
    const { connection } = createConnection(GOOD);
    const source = { type: 'salesforce', connectionId: connection.id };
    createAssessment({ source, objects: ['Lead'] });
    expect(createAssessment({ source, objects: ['Case'] }).rejection.status).toBe(409);
  });

  it('validates the object list', () => {
    const { connection } = createConnection(GOOD);
    const source = { type: 'salesforce', connectionId: connection.id };
    expect(createAssessment({ source, objects: [] }).rejection.status).toBe(400);
    expect(createAssessment({ source, objects: ['Lead', 'Lead'] }).rejection.status).toBe(400);
    expect(createAssessment({ source, objects: ['Nope__c'] }).rejection.status).toBe(400);
  });
});

describe('downloading the assessed data', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('is refused until profiling finishes, then gives every field with Id first', () => {
    vi.useFakeTimers();
    const { connection } = createConnection(GOOD);
    const { result } = createAssessment({
      source: { type: 'salesforce', connectionId: connection.id },
      objects: ['Contact', 'Invoice__c'],
    });
    const assessment = findAssessment(result.id);
    const contactId = result.runs.find((r) => r.object === 'Contact').id;

    expect(validateDataAccess(findRun(contactId)).status).toBe(409);
    expect(assessmentDataFiles(assessment).rejection.status).toBe(409);

    vi.advanceTimersByTime(PROFILE_MS);
    const contact = findRun(contactId);
    expect(validateDataAccess(contact)).toBeNull();

    const csv = runDataCsv(contact);
    expect(csv.charCodeAt(0)).toBe(0xfeff);
    const header = csv.slice(1).split('\r\n')[0].split(',');
    expect(header[0]).toBe('Id');
    // Every field, not only the CDEs.
    runProfile(contact).columns.forEach((column) => expect(header).toContain(column.name));
    expect(runDataFilename(contact)).toMatch(/_Contact\.csv$/);

    // The ZIP holds only objects with data: the failed Invoice is left out.
    const { files, filename } = assessmentDataFiles(assessment).result;
    expect(files.map((f) => f.name)).toEqual(['Contact.csv']);
    expect(filename).toMatch(/-data\.zip$/);
  });
});
