import { fetchJson, fetchBlob, apiUrl } from './client';

/**
 * The five endpoints from FRONTEND.md. This module is the only place an endpoint
 * string appears. Payload shapes (documented here since there are no types):
 *
 *   POST /runs                          -> { id, file, status: 'processing' }
 *   GET  /runs                          -> [{ id, file, status, overall? }]
 *   GET  /runs/{id}                     -> { id, file, status, createdAt, ...state }
 *        status 'processing'            -> + { stage, progress }        progress 0..1
 *             Runs only through Ingesting + Profiling before pausing -- see
 *             'awaiting_cdes' below. This same status/shape is reused for
 *             Evaluating + Scoring after a CDE set is confirmed.
 *        status 'awaiting_cdes'         -> + { records, columns, cdes }
 *             Profiling finished and detected `cdes` CDEs out of `columns`
 *             total columns. Nothing gets scored until PUT .../cdes confirms
 *             a selection (see below) -- this status has no `scores`/`overall`
 *             yet. Fetch GET .../profile now to show the reviewable table.
 *        status 'failed'                -> + { error }
 *        status 'completed'             -> + { overall, records, cdes, scores: {dimKey: number | null},
 *                                             columns, sampled, sampledRows, parseWarnings,
 *                                             cdeOverridden, notAssessed?: {dimKey: reason} }
 *             A dimension the engine couldn't evaluate (e.g. timeliness with no
 *             date column) reports null in `scores`, not 0 -- see lib/band.js.
 *             `columns`/`sampled`/`sampledRows`/`parseWarnings`/`cdeOverridden`
 *             are additive per API_CONTRACT.md -- all display-only.
 *   GET  /runs/{id}/dimensions/{dim}    -> { key, score, rules: [{ id, name, passRate,
 *                                             column, severity, evaluated, failed }] }
 *        not assessed                   -> { key, score: null, rules: [], notAssessedReason }
 *   GET  /runs/{id}/profile             -> { columns: [{ name, inferredType, semanticType,
 *                                             fillRate, distinctRatio, sampleValues,
 *                                             isCde, cdeScore, cdeReason }] }
 *             Available once status is 'awaiting_cdes' OR 'completed' -- NOT
 *             only after completion. 409 before that (still Ingesting/Profiling).
 *   PUT  /runs/{id}/cdes  { columns: [name, ...] }
 *                                        -> 202 { id, status: 'processing' }
 *             The ONE way a run leaves 'awaiting_cdes': confirms which columns
 *             are CDEs and starts Evaluating + Scoring. Also reusable later on
 *             a 'completed' run, to re-review and re-score with a different
 *             selection -- same endpoint, same effect either way. The run's
 *             poll picks up the transition back to 'completed' the same way
 *             it does for a brand-new run. 400 for an unknown column name or
 *             an empty selection, 409 if the run is neither 'awaiting_cdes'
 *             nor 'completed'.
 *   GET  /runs/{id}/dimensions/{dim}/rules/{ruleId}/examples?limit=10
 *                                        -> { ruleId, ruleName, passRate, total,
 *                                             examples: [{ row, column, value, reason }] }
 *   GET  /runs/{id}/report?type=...     -> PDF, Content-Disposition: attachment
 *   DELETE /runs/{id}                   -> 204
 */

export function listRuns() {
  return fetchJson('/runs');
}

export function getRun(id) {
  return fetchJson(`/runs/${encodeURIComponent(id)}`);
}

export function getDimension(id, dimensionKey) {
  return fetchJson(
    `/runs/${encodeURIComponent(id)}/dimensions/${encodeURIComponent(dimensionKey)}`,
  );
}

// Lazy: only fetched when the column profile panel is opened from the run
// summary, never preloaded alongside the rest of the run.
export function getRunProfile(id) {
  return fetchJson(`/runs/${encodeURIComponent(id)}/profile`);
}

// Lazy, per-rule: only fetched once a rule is opened in the drawer, never
// preloaded alongside the rest of the dimension.
export function getRuleExamples(id, dimensionKey, ruleId, limit = 10) {
  return fetchJson(
    `/runs/${encodeURIComponent(id)}/dimensions/${encodeURIComponent(dimensionKey)}` +
      `/rules/${encodeURIComponent(ruleId)}/examples?limit=${limit}`,
  );
}

// Confirms which columns count as CDEs and starts (or re-starts) scoring.
// Used two ways: the required first confirmation out of 'awaiting_cdes'
// (AwaitingCdesView), and an optional later re-review of a 'completed' run
// (ColumnProfilePanel). Returns while the run is `processing` again --
// HomePanel's existing poll picks up the transition back to `completed` with
// new scores, same as any other run.
export function overrideCdes(id, columns) {
  return fetchJson(`/runs/${encodeURIComponent(id)}/cdes`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ columns }),
  });
}

export function uploadRun(file) {
  const body = new FormData();
  body.append('file', file);
  // No Content-Type header on purpose: the browser must set the multipart
  // boundary itself.
  return fetchJson('/runs', { method: 'POST', body });
}

export function deleteRun(id) {
  return fetchJson(`/runs/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

// Deliberately returns a URL string, not a fetch. In production this is what
// the two <a href download> links point at, so the browser streams the file
// itself instead of it being buffered in JS memory.
export function reportUrl(id, type) {
  return apiUrl(`/runs/${encodeURIComponent(id)}/report?type=${encodeURIComponent(type)}`);
}

// Used instead of the plain link, but only in dev (see DownloadMenu). A
// Service Worker (which is how the MSW mock intercepts requests) doesn't
// reliably see requests made by clicking an <a download>, so those clicks
// fall through to Vite's dev server and download its index.html shell
// instead of the mocked report. A real fetch() is what MSW actually
// documents intercepting, so this forces the download through one.
export async function downloadReport(id, type) {
  const path = `/runs/${encodeURIComponent(id)}/report?type=${encodeURIComponent(type)}`;
  const { blob, filename } = await fetchBlob(path);

  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename || `report-${type}.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Give the browser a moment to pick up the blob before freeing it.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
