import { STATUS, DIMENSION_KEYS, CONNECTION_STATE, MAX_ASSESSMENT_OBJECTS } from '@/api/constants';
import {
  applyCdeOverride,
  createObjectRun,
  detailOf,
  findRun,
  removeRun,
  runDataCsv,
  validateCdeOverride,
  validateDataAccess,
} from './db';

// Revision 5 in-memory stand-in: Salesforce connections and multi-object
// assessments. Like db.js, rules live in plain functions returning either a
// result or { status, errorCode?, error }, so handlers.js stays a thin HTTP
// wrapper and the rules are testable without going through fetch.
//
// Demo triggers, so every error state in the contract is reachable without a
// real org (API_CONTRACT.md "Mocking the flow"):
//   address not *.my.salesforce.com / *.lightning.force.com -> invalid_url
//   address containing "unreachable"                          -> unreachable
//   client ID or secret containing "invalid"                  -> invalid_client
//   client ID containing "noflow" / "norunas" / "noapi"       -> flow_not_enabled /
//                                                               no_run_as_user / api_disabled
//   client ID containing "refused"                            -> failed
//   selecting Invoice__c                                      -> that object fails,
//                                                               the assessment goes partial

const IDLE_MS = 30 * 60 * 1000;

const connections = new Map();
const assessments = new Map([
  [
    'asm_sf_1',
    {
      id: 'asm_sf_1',
      name: 'Acme Corporation',
      connectionId: 'sfc_1',
      source: {
        type: 'salesforce',
        orgName: 'Acme Corporation',
        orgId: '00D12345678901',
        environment: 'production',
      },
      runIds: ['run_sf_1', 'run_sf_2'],
      createdAt: new Date(Date.now() - 40 * 60_000).toISOString(),
    },
  ],
]);

let seq = 0;
function nextId(prefix) {
  seq += 1;
  return `${prefix}_${Date.now().toString(36)}${seq}`;
}

// ---------------------------------------------------------------------------
// Connections
// ---------------------------------------------------------------------------

// Accepts what a user copies from the browser's address bar, with or without
// https://, for production and sandbox, My Domain or Lightning. Only
// Salesforce's own domains are accepted -- a security control on the real
// backend, because the secret is sent to that address.
export function parseInstanceUrl(input) {
  const host = String(input ?? '')
    .trim()
    .replace(/^https?:\/\//i, '')
    .split('/')[0]
    .toLowerCase();

  const match = host.match(
    /^([a-z0-9-]+?)(?:--([a-z0-9]+))?(\.sandbox)?\.(my\.salesforce\.com|lightning\.force\.com)$/,
  );
  if (!match) return null;

  const [, domain, sandboxName, sandboxPart] = match;
  const isSandbox = Boolean(sandboxName || sandboxPart);
  const myDomain = sandboxName ? `${domain}--${sandboxName}` : domain;
  return {
    host,
    domain,
    environment: isSandbox ? 'sandbox' : 'production',
    instanceUrl: isSandbox
      ? `https://${myDomain}.sandbox.my.salesforce.com`
      : `https://${myDomain}.my.salesforce.com`,
  };
}

const FIELD_LABELS = { instanceUrl: 'Salesforce address', clientId: 'Client ID', clientSecret: 'Client secret' };

export function validateConnectionRequest(body) {
  for (const field of ['instanceUrl', 'clientId', 'clientSecret']) {
    if (!String(body?.[field] ?? '').trim()) {
      return { status: 400, error: `${FIELD_LABELS[field]} is required.` };
    }
  }

  const parsed = parseInstanceUrl(body.instanceUrl);
  if (!parsed) {
    return {
      status: 400,
      errorCode: 'invalid_url',
      error:
        'That isn’t a Salesforce address. Use the address from your browser while signed in to Salesforce, for example acme.my.salesforce.com or acme.lightning.force.com.',
    };
  }

  const { clientId, clientSecret } = body;
  if (parsed.host.includes('unreachable')) {
    return {
      status: 502,
      errorCode: 'unreachable',
      error: `Nothing answered at ${parsed.host}. Check the Salesforce address, or your network connection.`,
    };
  }
  if (/invalid/i.test(clientId) || /invalid/i.test(clientSecret)) {
    return {
      status: 422,
      errorCode: 'invalid_client',
      error:
        'Salesforce rejected the client ID or secret. Check both were copied in full. New credentials can also take a few minutes to start working after the app is saved.',
    };
  }
  if (/noflow/i.test(clientId)) {
    return {
      status: 422,
      errorCode: 'flow_not_enabled',
      error: `The app at ${parsed.host} isn’t set up for the client credentials flow. In the app’s settings, tick Enable Client Credentials Flow under Flow Enablement and again under the OAuth Policies.`,
    };
  }
  if (/norunas/i.test(clientId)) {
    return {
      status: 422,
      errorCode: 'no_run_as_user',
      error: 'The app has no Run As user. On the app’s Policies tab, set the Run As (Username) under OAuth Policies.',
    };
  }
  if (/noapi/i.test(clientId)) {
    return {
      status: 422,
      errorCode: 'api_disabled',
      error:
        'This org or its Run As user has no API access. That is typical of Professional and Essentials editions without the API add-on — ask the Salesforce administrator.',
    };
  }
  if (/refused/i.test(clientId)) {
    return {
      status: 422,
      errorCode: 'failed',
      error: 'Salesforce refused the request: user hasn’t approved this consumer.',
    };
  }

  return null;
}

function orgNameFor(domain) {
  return `${domain.charAt(0).toUpperCase()}${domain.slice(1)} Corporation`;
}

// The secret is validated and then dropped -- the connection object never
// holds it, mirroring the contract's "never returned, never written".
export function createConnection(body) {
  const rejection = validateConnectionRequest(body);
  if (rejection) return { rejection };

  const parsed = parseInstanceUrl(body.instanceUrl);
  const now = Date.now();
  const connection = {
    id: nextId('sfc'),
    state: CONNECTION_STATE.CONNECTED,
    environment: parsed.environment,
    orgId: `00D${Math.random().toString(36).slice(2, 14).toUpperCase()}`,
    orgName: orgNameFor(parsed.domain),
    orgEdition: 'Enterprise Edition',
    instanceUrl: parsed.instanceUrl,
    username: `integration@${parsed.domain}.com`,
    connectedAt: new Date(now).toISOString(),
    expiresAt: new Date(now + IDLE_MS).toISOString(),
    closedReason: null,
    assessmentId: null,
  };
  connections.set(connection.id, connection);
  return { connection };
}

function close(connection, reason) {
  connection.state = CONNECTION_STATE.CLOSED;
  connection.closedReason = reason;
  connection.expiresAt = null;
}

export function findConnection(id) {
  const connection = connections.get(id);
  if (!connection) return null;
  // An unused connection closes after 30 minutes.
  if (
    connection.state === CONNECTION_STATE.CONNECTED &&
    !connection.assessmentId &&
    Date.now() > Date.parse(connection.expiresAt)
  ) {
    close(connection, 'idle');
  }
  return connection;
}

const OBJECT_CATALOG = [
  { name: 'Account', label: 'Account', custom: false, recordCount: 5790, suggested: true },
  { name: 'Contact', label: 'Contact', custom: false, recordCount: 1500, suggested: true },
  { name: 'Lead', label: 'Lead', custom: false, recordCount: 3420, suggested: true },
  { name: 'Opportunity', label: 'Opportunity', custom: false, recordCount: null, suggested: true },
  { name: 'Case', label: 'Case', custom: false, recordCount: 8210, suggested: true },
  { name: 'Campaign', label: 'Campaign', custom: false, recordCount: 46, suggested: false },
  { name: 'Order', label: 'Order', custom: false, recordCount: 2211, suggested: false },
  { name: 'Product2', label: 'Product', custom: false, recordCount: 612, suggested: false },
  { name: 'Invoice__c', label: 'Invoice', custom: true, recordCount: 12044, suggested: false },
  { name: 'Project__c', label: 'Project', custom: true, recordCount: 318, suggested: false },
];

const SYSTEM_OBJECTS = [
  { name: 'AccountHistory', label: 'Account History', custom: false, recordCount: 40211, suggested: false },
  { name: 'ContactShare', label: 'Contact Share', custom: false, recordCount: 3001, suggested: false },
  { name: 'AccountFeed', label: 'Account Feed', custom: false, recordCount: null, suggested: false },
];

const HIDDEN_COUNT = 612;

// Objects that fail during download, with the per-object error the
// assessment reports while the rest carry on.
const OBJECT_FAILURES = {
  Invoice__c: 'Salesforce refused to return Invoice records to the Run As user.',
};

export function listObjects(connection, { includeAll = false } = {}) {
  if (connection.state !== CONNECTION_STATE.CONNECTED) {
    return { rejection: { status: 409, error: 'This connection is closed. Connect again to pick objects.' } };
  }
  return {
    result: {
      objects: includeAll ? [...OBJECT_CATALOG, ...SYSTEM_OBJECTS] : OBJECT_CATALOG,
      hiddenCount: includeAll ? 0 : HIDDEN_COUNT,
    },
  };
}

function isDownloading(assessment) {
  return assessment.runIds.some((id) => {
    const run = findRun(id);
    return run && run.status === STATUS.PROCESSING && detailOf(run).stage === 'Ingesting';
  });
}

export function disconnect(id) {
  const connection = findConnection(id);
  if (!connection) return { rejection: { status: 404, error: 'Connection not found.' } };
  const assessment = connection.assessmentId && assessments.get(connection.assessmentId);
  if (assessment && isDownloading(assessment)) {
    return {
      rejection: {
        status: 409,
        error: 'An assessment is still downloading from this connection. Delete the assessment instead.',
      },
    };
  }
  if (connection.state === CONNECTION_STATE.CONNECTED) close(connection, 'disconnected');
  return {};
}

// ---------------------------------------------------------------------------
// Assessments
// ---------------------------------------------------------------------------

const ALL_OBJECT_NAMES = new Set([...OBJECT_CATALOG, ...SYSTEM_OBJECTS].map((o) => o.name));

export function createAssessment(body) {
  if (body?.source?.type !== 'salesforce') {
    return { rejection: { status: 400, error: 'source.type must be "salesforce".' } };
  }
  const connection = findConnection(body.source.connectionId);
  if (!connection) return { rejection: { status: 404, error: 'Connection not found.' } };
  if (connection.state !== CONNECTION_STATE.CONNECTED || connection.assessmentId) {
    return {
      rejection: { status: 409, error: 'This connection is closed or already feeds another assessment. Connect again.' },
    };
  }

  const objects = Array.isArray(body.objects) ? body.objects : [];
  if (objects.length === 0) return { rejection: { status: 400, error: 'Select at least one object.' } };
  if (objects.length > MAX_ASSESSMENT_OBJECTS) {
    return { rejection: { status: 400, error: `Select at most ${MAX_ASSESSMENT_OBJECTS} objects.` } };
  }
  const duplicate = objects.find((name, i) => objects.indexOf(name) !== i);
  if (duplicate) return { rejection: { status: 400, error: `${duplicate} is selected more than once.` } };
  const unknown = objects.find((name) => !ALL_OBJECT_NAMES.has(name));
  if (unknown) return { rejection: { status: 400, error: `${unknown} isn’t an object in this org.` } };

  const id = nextId('asm');
  const name = connection.environment === 'sandbox' ? `${connection.orgName} (Sandbox)` : connection.orgName;
  const catalog = [...OBJECT_CATALOG, ...SYSTEM_OBJECTS];
  const runIds = objects.map((objectName) => {
    const meta = catalog.find((o) => o.name === objectName);
    return createObjectRun({
      assessmentId: id,
      orgName: connection.orgName,
      object: objectName,
      label: meta.label,
      recordCount: meta.recordCount ?? undefined,
      failReason: OBJECT_FAILURES[objectName],
    }).id;
  });

  const assessment = {
    id,
    name,
    connectionId: connection.id,
    source: {
      type: 'salesforce',
      orgName: connection.orgName,
      orgId: connection.orgId,
      environment: connection.environment,
    },
    runIds,
    createdAt: new Date().toISOString(),
  };
  assessments.set(id, assessment);
  connection.assessmentId = id;
  connection.expiresAt = null;

  const runs = runIds.map((runId) => {
    const run = findRun(runId);
    return { id: run.id, object: run.source.object, label: run.source.label, status: run.status };
  });
  return { result: { id, name, status: STATUS.PROCESSING, runs } };
}

function runsOf(assessment) {
  return assessment.runIds.map(findRun).filter(Boolean);
}

// API_CONTRACT.md "How the assessment's status follows its objects".
export function statusOf(runStatuses) {
  if (runStatuses.some((s) => s === STATUS.PROCESSING)) return STATUS.PROCESSING;
  if (runStatuses.some((s) => s === STATUS.AWAITING_CDES)) return STATUS.AWAITING_CDES;
  if (runStatuses.some((s) => s === STATUS.COMPLETED)) return STATUS.COMPLETED;
  return STATUS.FAILED;
}

// Each overall dimension across every completed object, as if they were one
// dataset: objects weigh in proportion to how many records were checked.
// A dimension is scored overall if at least one object could assess it.
export function overallScores(completedRuns) {
  const scores = {};
  const notAssessed = {};
  DIMENSION_KEYS.forEach((key) => {
    const contributing = completedRuns.filter((run) => typeof run.scores?.[key] === 'number');
    if (contributing.length === 0) {
      scores[key] = null;
      notAssessed[key] = 'No selected object could be assessed on this dimension.';
      return;
    }
    const weight = contributing.reduce((sum, run) => sum + run.records, 0);
    const weighted = contributing.reduce((sum, run) => sum + run.scores[key] * run.records, 0);
    scores[key] = Math.round((weighted / weight) * 10) / 10;
  });
  const values = Object.values(scores).filter((v) => typeof v === 'number');
  const overall = values.length
    ? Math.round((values.reduce((a, b) => a + b, 0) / values.length) * 10) / 10
    : null;
  return { scores, notAssessed, overall };
}

// The connection serves exactly one assessment: once every object is past
// its download, the secret would be discarded and the token revoked.
function closeWhenExtracted(assessment) {
  const connection = connections.get(assessment.connectionId);
  if (connection?.state === CONNECTION_STATE.CONNECTED && !isDownloading(assessment)) {
    close(connection, 'extracted');
  }
}

function entryFor(run) {
  const entry = { ...detailOf(run), object: run.source.object, label: run.source.label };
  delete entry.file;
  delete entry.source;
  delete entry.assessmentId;
  delete entry.createdAt;
  return entry;
}

export function findAssessment(id) {
  return assessments.get(id) ?? null;
}

export function assessmentDetail(assessment) {
  const runs = runsOf(assessment);
  closeWhenExtracted(assessment);
  const status = statusOf(runs.map((r) => r.status));
  const base = {
    id: assessment.id,
    name: assessment.name,
    status,
    source: assessment.source,
    runs: runs.map(entryFor),
  };

  if (status === STATUS.PROCESSING) {
    const progress =
      runs.reduce((sum, run) => sum + (run.status === STATUS.PROCESSING ? detailOf(run).progress : 1), 0) /
      runs.length;
    return { ...base, progress: Math.round(progress * 100) / 100 };
  }

  if (status !== STATUS.COMPLETED) return base;

  const completed = runs.filter((r) => r.status === STATUS.COMPLETED);
  const { scores, notAssessed, overall } = overallScores(completed);
  return {
    ...base,
    overall,
    records: completed.reduce((sum, run) => sum + run.records, 0),
    objects: runs.length,
    partial: completed.length < runs.length,
    scores,
    notAssessed,
  };
}

export function assessmentSummary(assessment) {
  const detail = assessmentDetail(assessment);
  return {
    id: detail.id,
    name: detail.name,
    status: detail.status,
    createdAt: assessment.createdAt,
    objects: assessment.runIds.length,
    ...(detail.status === STATUS.COMPLETED ? { overall: detail.overall } : {}),
  };
}

export function allAssessments() {
  return [...assessments.values()].reverse().map(assessmentSummary);
}

// PUT /assessments/{id}/cdes. From awaiting_cdes every awaiting object must
// be present; from completed, only the objects to re-score. Validates every
// list before applying any, so a rejected request starts nothing.
export function confirmAssessmentCdes(assessment, body) {
  const runs = runsOf(assessment);
  const status = statusOf(runs.map((r) => r.status));
  if (![STATUS.AWAITING_CDES, STATUS.COMPLETED].includes(status)) {
    return { rejection: { status: 409, error: 'This assessment is not awaiting confirmation or completed.' } };
  }

  const lists = body?.objects && typeof body.objects === 'object' ? body.objects : {};
  const byObject = new Map(runs.map((run) => [run.source.object, run]));

  for (const objectName of Object.keys(lists)) {
    const run = byObject.get(objectName);
    if (!run) {
      return { rejection: { status: 400, error: `${objectName} isn’t part of this assessment.` } };
    }
    const ready = status === STATUS.AWAITING_CDES ? STATUS.AWAITING_CDES : STATUS.COMPLETED;
    if (run.status !== ready) {
      return { rejection: { status: 400, error: `${run.source.label} isn’t ready to score.` } };
    }
    const rejection = validateCdeOverride(run, lists[objectName], { viaAssessment: true });
    if (rejection) {
      return { rejection: { status: 400, error: `${run.source.label}: ${rejection.error}` } };
    }
  }

  if (status === STATUS.AWAITING_CDES) {
    const missing = runs.find((run) => run.status === STATUS.AWAITING_CDES && !lists[run.source.object]);
    if (missing) {
      return { rejection: { status: 400, error: `Confirm the columns for ${missing.source.label} too.` } };
    }
  } else if (Object.keys(lists).length === 0) {
    return { rejection: { status: 400, error: 'Include at least one object to re-score.' } };
  }

  Object.entries(lists).forEach(([objectName, columns]) => {
    applyCdeOverride(byObject.get(objectName), columns);
  });
  return { result: { id: assessment.id, status: STATUS.PROCESSING } };
}

export function deleteAssessment(id) {
  const assessment = assessments.get(id);
  if (!assessment) return false;
  assessment.runIds.forEach(removeRun);
  const connection = connections.get(assessment.connectionId);
  if (connection?.state === CONNECTION_STATE.CONNECTED) close(connection, 'disconnected');
  assessments.delete(id);
  return true;
}

// A run-shaped stand-in for the whole assessment, so the existing PDF
// generator can render the overall report.
export function reportSubject(assessment) {
  const detail = assessmentDetail(assessment);
  const completed = runsOf(assessment).filter((r) => r.status === STATUS.COMPLETED);
  return {
    id: assessment.id,
    file: detail.name,
    overall: detail.overall,
    records: detail.records,
    cdes: completed.reduce((sum, run) => sum + run.cdes, 0),
    scores: detail.scores,
    notAssessed: detail.notAssessed,
  };
}

// GET /assessments/{id}/data: one CSV per object that has data, named by API
// name. 409 while any object is still downloading or first profiling, or when
// every object failed.
export function assessmentDataFiles(assessment) {
  const runs = runsOf(assessment);
  const stillWorking = runs.some((run) => run.status === STATUS.PROCESSING && validateDataAccess(run));
  if (stillWorking) {
    return { rejection: { status: 409, error: 'The data is still being downloaded or profiled. Try again once every object is profiled.' } };
  }
  const ready = runs.filter((run) => !validateDataAccess(run));
  if (ready.length === 0) {
    return { rejection: { status: 409, error: 'No object in this assessment has data to download.' } };
  }
  return {
    result: {
      filename: `${assessment.source.orgName.replace(/[^\w-]+/g, '_')}-data.zip`,
      files: ready.map((run) => ({ name: `${run.source.object}.csv`, content: runDataCsv(run) })),
    },
  };
}
