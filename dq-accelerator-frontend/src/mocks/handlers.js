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
  runProfile,
  summaryOf,
  validateCdeOverride,
  validateProfileAccess,
  validateUploadFile,
} from './db';
import { buildReportPdf } from './report';

const url = (path) => `${API_BASE}${path}`;

export const handlers = [
  http.get(url('/runs'), async () => {
    await delay(120);
    return HttpResponse.json(allRuns().map(summaryOf));
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
];
