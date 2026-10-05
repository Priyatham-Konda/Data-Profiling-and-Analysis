import { fetchJson, apiUrl, downloadFile } from './client';

/**
 * Revision 5: Salesforce connections and multi-object assessments. Payload
 * shapes (documented here since there are no types):
 *
 *   POST   /salesforce/connections  { instanceUrl, clientId, clientSecret }
 *                                   -> 201 connection (shape below)
 *          errors carry { errorCode, error }: invalid_url, invalid_client,
 *          flow_not_enabled, no_run_as_user, api_disabled, unreachable, failed
 *   GET    /salesforce/connections/{id}
 *                                   -> { id, state, environment, orgId, orgName,
 *                                        orgEdition, instanceUrl, username,
 *                                        connectedAt, expiresAt, closedReason,
 *                                        assessmentId }
 *   GET    /salesforce/connections/{id}/objects[?include=all]
 *                                   -> { objects: [{ name, label, custom,
 *                                        recordCount, suggested }], hiddenCount }
 *   DELETE /salesforce/connections/{id}           -> 204
 *
 *   POST   /assessments  { source: { type: 'salesforce', connectionId }, objects: [name] }
 *                                   -> 201 { id, name, status, runs: [{ id, object, label, status }] }
 *   GET    /assessments             -> [{ id, name, status, overall?, objects }]
 *   GET    /assessments/{id}        -> { id, name, status, source, ...state, runs: [...] }
 *          each entry in runs has the GET /runs/{id} shape for its status,
 *          without `file`, with `object` and `label` added
 *   PUT    /assessments/{id}/cdes  { objects: { [objectName]: [column, ...] } }
 *                                   -> 202 { id, status: 'processing' }
 *   GET    /assessments/{id}/report?type=summary|in-depth  -> PDF
 *   GET    /assessments/{id}/data   -> ZIP, one CSV per object (409 until profiled)
 *   DELETE /assessments/{id}        -> 204
 *
 * The client secret is sent exactly once, in the body of createConnection,
 * and is never returned, stored, or logged -- see SalesforcePanel.
 */

export function createConnection({ instanceUrl, clientId, clientSecret }) {
  return fetchJson('/salesforce/connections', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ instanceUrl, clientId, clientSecret }),
  });
}

export function getConnection(id) {
  return fetchJson(`/salesforce/connections/${encodeURIComponent(id)}`);
}

export function listObjects(id, { includeAll = false } = {}) {
  const query = includeAll ? '?include=all' : '';
  return fetchJson(`/salesforce/connections/${encodeURIComponent(id)}/objects${query}`);
}

export function deleteConnection(id) {
  return fetchJson(`/salesforce/connections/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function createAssessment(connectionId, objects) {
  return fetchJson('/assessments', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source: { type: 'salesforce', connectionId }, objects }),
  });
}

export function listAssessments() {
  return fetchJson('/assessments');
}

export function getAssessment(id) {
  return fetchJson(`/assessments/${encodeURIComponent(id)}`);
}

// `objects` maps object name -> confirmed column names, e.g.
// { Account: ['Name', 'Phone'], Contact: ['Email'] }.
export function confirmAssessmentCdes(id, objects) {
  return fetchJson(`/assessments/${encodeURIComponent(id)}/cdes`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ objects }),
  });
}

export function deleteAssessment(id) {
  return fetchJson(`/assessments/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

// Same reasoning as reportUrl/downloadReport in runs.js: a plain link in
// production, a fetch()-then-blob in dev where MSW can't see anchor clicks.
export function assessmentReportUrl(id, type) {
  return apiUrl(`/assessments/${encodeURIComponent(id)}/report?type=${encodeURIComponent(type)}`);
}

export function downloadAssessmentReport(id, type) {
  return downloadFile(
    `/assessments/${encodeURIComponent(id)}/report?type=${encodeURIComponent(type)}`,
    `assessment-${type}.pdf`,
  );
}

// GET /assessments/{id}/data: one CSV per object, zipped.
export function assessmentDataUrl(id) {
  return apiUrl(`/assessments/${encodeURIComponent(id)}/data`);
}

export function downloadAssessmentData(id) {
  return downloadFile(`/assessments/${encodeURIComponent(id)}/data`, 'assessment-data.zip');
}
