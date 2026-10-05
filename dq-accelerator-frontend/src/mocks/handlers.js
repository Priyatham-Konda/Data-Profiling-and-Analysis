import { http, HttpResponse, delay } from 'msw';
import { API_BASE } from '@/api/client';
import { STATUS } from '@/api/constants';
import {
  allRuns,
  applyCdeOverride,
  createRun,
  detailOf,
  dimensionDetail,
  findRun,
  removeRun,
  ruleExamples,
  runDataCsv,
  runDataFilename,
  runProfile,
  summaryOf,
  validateDataAccess,
  validateCdeOverride,
  validateProfileAccess,
  validateUploadFile,
} from './db';
import { buildReportPdf } from './report';
import * as sfdc from './salesforce';
import { buildZip } from './zip';

const url = (path) => `${API_BASE}${path}`;

function rejectionResponse({ status, errorCode, error }) {
  return HttpResponse.json(errorCode ? { errorCode, error } : { error }, { status });
}

export const handlers = [
  http.get(url('/runs'), async ({ request }) => {
    await delay(120);
    // Revision 5: object runs belong to assessments and are listed there;
    // ?include=all adds them, each with its assessmentId.
    const includeObjectRuns = new URL(request.url).searchParams.get('include') === 'all';
    return HttpResponse.json(
      allRuns({ includeObjectRuns }).map((run) => ({
        ...summaryOf(run),
        ...(run.assessmentId ? { assessmentId: run.assessmentId } : {}),
      })),
    );
  }),

  http.post(url('/runs'), async ({ request }) => {
    const form = await request.formData();
    const file = form.get('file');
    if (!file) {
      return HttpResponse.json({ error: 'No file provided.' }, { status: 400 });
    }

    // Authoritative limits live in db.js, not on the client. validateCsv.js
    // only does an instant, obvious sanity check (a file was picked, it isn't
    // literally empty); real acceptance -- size, extension, content -- is
    // decided by the backend, per API_CONTRACT.md.
    const rejection = validateUploadFile(file);
    if (rejection) {
      return HttpResponse.json({ error: rejection.error }, { status: rejection.status });
    }

    // Long enough that the user visibly lands on Home before the toast arrives.
    await delay(700);
    const run = createRun(file.name);
    return HttpResponse.json(summaryOf(run), { status: 201 });
  }),

  http.get(url('/runs/:id'), async ({ params }) => {
    await delay(120);
    const run = findRun(params.id);
    if (!run) return HttpResponse.json({ error: 'Run not found.' }, { status: 404 });
    return HttpResponse.json(detailOf(run));
  }),

  http.delete(url('/runs/:id'), async ({ params }) => {
    await delay(200);
    if (findRun(params.id)?.assessmentId) {
      return HttpResponse.json(
        { error: 'This object belongs to a Salesforce assessment. Delete the assessment instead.' },
        { status: 409 },
      );
    }
    const removed = removeRun(params.id);
    if (!removed) return HttpResponse.json({ error: 'Run not found.' }, { status: 404 });
    return new HttpResponse(null, { status: 204 });
  }),

  http.get(url('/runs/:id/dimensions/:dim'), async ({ params }) => {
    await delay(320);
    const run = findRun(params.id);
    if (!run) return HttpResponse.json({ error: 'Run not found.' }, { status: 404 });
    const detail = dimensionDetail(run, params.dim);
    if (!detail) {
      return HttpResponse.json({ error: 'Dimension not available.' }, { status: 404 });
    }
    return HttpResponse.json(detail);
  }),

  http.get(url('/runs/:id/dimensions/:dim/rules/:ruleId/examples'), async ({ params, request }) => {
    await delay(280);
    const run = findRun(params.id);
    if (!run) return HttpResponse.json({ error: 'Run not found.' }, { status: 404 });

    const limit = Number(new URL(request.url).searchParams.get('limit')) || 10;
    const result = ruleExamples(run, params.dim, params.ruleId, limit);
    if (!result) return HttpResponse.json({ error: 'Rule not found.' }, { status: 404 });
    return HttpResponse.json(result);
  }),

  http.get(url('/runs/:id/profile'), async ({ params }) => {
    await delay(250);
    const run = findRun(params.id);
    if (!run) return HttpResponse.json({ error: 'Run not found.' }, { status: 404 });
    // Available one stage earlier than it used to be: profiling itself is
    // what produces this data, so it's ready the moment the run reaches
    // awaiting_cdes, not only once the run is fully completed.
    const rejection = validateProfileAccess(run);
    if (rejection) {
      return HttpResponse.json({ error: rejection.error }, { status: rejection.status });
    }
    return HttpResponse.json(runProfile(run));
  }),

  http.put(url('/runs/:id/cdes'), async ({ params, request }) => {
    await delay(200);
    const run = findRun(params.id);
    if (!run) return HttpResponse.json({ error: 'Run not found.' }, { status: 404 });

    const body = await request.json().catch(() => null);
    const columns = Array.isArray(body?.columns) ? body.columns : [];

    const rejection = validateCdeOverride(run, columns);
    if (rejection) {
      return HttpResponse.json({ error: rejection.error }, { status: rejection.status });
    }

    applyCdeOverride(run, columns);
    return HttpResponse.json({ id: run.id, status: STATUS.PROCESSING }, { status: 202 });
  }),

  http.get(url('/runs/:id/report'), async ({ params, request }) => {
    const type = new URL(request.url).searchParams.get('type') === 'in-depth' ? 'in-depth' : 'summary';
    const run = findRun(params.id);
    if (!run) return HttpResponse.json({ error: 'Run not found.' }, { status: 404 });
    if (run.status !== STATUS.COMPLETED) {
      return HttpResponse.json(
        { error: 'The report is only available once the run has completed.' },
        { status: 409 },
      );
    }

    const bytes = await buildReportPdf(run, type);
    const name = `${run.file.replace(/\.csv$/i, '')}-${type}.pdf`;
    return new HttpResponse(bytes, {
      headers: {
        'Content-Type': 'application/pdf',
        'Content-Disposition': `attachment; filename="${name}"`,
      },
    });
  }),

  // Revision 5 addendum: the data the run was assessed on, as CSV.
  http.get(url('/runs/:id/data'), async ({ params }) => {
    const run = findRun(params.id);
    if (!run) return HttpResponse.json({ error: 'Run not found.' }, { status: 404 });
    const rejection = validateDataAccess(run);
    if (rejection) return rejectionResponse(rejection);
    return new HttpResponse(runDataCsv(run), {
      headers: {
        'Content-Type': 'text/csv; charset=utf-8',
        'Content-Disposition': `attachment; filename="${runDataFilename(run)}"`,
      },
    });
  }),

  // -------------------------------------------------------------------------
  // Revision 5: Salesforce connections and assessments
  // -------------------------------------------------------------------------

  http.post(url('/salesforce/connections'), async ({ request }) => {
    await delay(900); // "answers within a second or two"
    const body = await request.json().catch(() => null);
    const { connection, rejection } = sfdc.createConnection(body);
    if (rejection) return rejectionResponse(rejection);
    return HttpResponse.json(connection, { status: 201 });
  }),

  http.get(url('/salesforce/connections/:id'), async ({ params }) => {
    await delay(120);
    const connection = sfdc.findConnection(params.id);
    if (!connection) return HttpResponse.json({ error: 'Connection not found.' }, { status: 404 });
    return HttpResponse.json(connection);
  }),

  http.get(url('/salesforce/connections/:id/objects'), async ({ params, request }) => {
    await delay(1200); // "takes one to three seconds"
    const connection = sfdc.findConnection(params.id);
    if (!connection) return HttpResponse.json({ error: 'Connection not found.' }, { status: 404 });
    const includeAll = new URL(request.url).searchParams.get('include') === 'all';
    const { result, rejection } = sfdc.listObjects(connection, { includeAll });
    if (rejection) return rejectionResponse(rejection);
    return HttpResponse.json(result);
  }),

  http.delete(url('/salesforce/connections/:id'), async ({ params }) => {
    await delay(150);
    const { rejection } = sfdc.disconnect(params.id);
    if (rejection) return rejectionResponse(rejection);
    return new HttpResponse(null, { status: 204 });
  }),

  http.post(url('/assessments'), async ({ request }) => {
    await delay(400);
    const body = await request.json().catch(() => null);
    const { result, rejection } = sfdc.createAssessment(body);
    if (rejection) return rejectionResponse(rejection);
    return HttpResponse.json(result, { status: 201 });
  }),

  http.get(url('/assessments'), async () => {
    await delay(120);
    return HttpResponse.json(sfdc.allAssessments());
  }),

  http.get(url('/assessments/:id'), async ({ params }) => {
    await delay(120);
    const assessment = sfdc.findAssessment(params.id);
    if (!assessment) return HttpResponse.json({ error: 'Assessment not found.' }, { status: 404 });
    return HttpResponse.json(sfdc.assessmentDetail(assessment));
  }),

  http.put(url('/assessments/:id/cdes'), async ({ params, request }) => {
    await delay(200);
    const assessment = sfdc.findAssessment(params.id);
    if (!assessment) return HttpResponse.json({ error: 'Assessment not found.' }, { status: 404 });
    const body = await request.json().catch(() => null);
    const { result, rejection } = sfdc.confirmAssessmentCdes(assessment, body);
    if (rejection) return rejectionResponse(rejection);
    return HttpResponse.json(result, { status: 202 });
  }),

  http.get(url('/assessments/:id/report'), async ({ params, request }) => {
    const type = new URL(request.url).searchParams.get('type') === 'in-depth' ? 'in-depth' : 'summary';
    const assessment = sfdc.findAssessment(params.id);
    if (!assessment) return HttpResponse.json({ error: 'Assessment not found.' }, { status: 404 });
    if (sfdc.assessmentDetail(assessment).status !== STATUS.COMPLETED) {
      return HttpResponse.json(
        { error: 'The report is only available once the assessment has completed.' },
        { status: 409 },
      );
    }
    const subject = sfdc.reportSubject(assessment);
    const bytes = await buildReportPdf(subject, type);
    return new HttpResponse(bytes, {
      headers: {
        'Content-Type': 'application/pdf',
        'Content-Disposition': `attachment; filename="${subject.file.replace(/[^\w -]+/g, '')}-${type}.pdf"`,
      },
    });
  }),

  http.get(url('/assessments/:id/data'), async ({ params }) => {
    const assessment = sfdc.findAssessment(params.id);
    if (!assessment) return HttpResponse.json({ error: 'Assessment not found.' }, { status: 404 });
    const { result, rejection } = sfdc.assessmentDataFiles(assessment);
    if (rejection) return rejectionResponse(rejection);
    return new HttpResponse(buildZip(result.files), {
      headers: {
        'Content-Type': 'application/zip',
        'Content-Disposition': `attachment; filename="${result.filename}"`,
      },
    });
  }),

  http.delete(url('/assessments/:id'), async ({ params }) => {
    await delay(200);
    if (!sfdc.deleteAssessment(params.id)) {
      return HttpResponse.json({ error: 'Assessment not found.' }, { status: 404 });
    }
    return new HttpResponse(null, { status: 204 });
  }),
];
