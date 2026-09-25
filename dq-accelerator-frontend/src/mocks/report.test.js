import { describe, expect, it, vi } from 'vitest';
import { PDFDocument } from 'pdf-lib';
import { buildReportPdf } from './report';
import { applyCdeOverride, createRun, findRun, runProfile } from './db';

// Drives a run through BOTH phases of the lifecycle: Ingesting + Profiling to
// awaiting_cdes, then a confirmation (accepting the auto-detected defaults)
// through Evaluating + Scoring to completed -- reports can only be generated
// for a completed run. Must read each resolved state BEFORE switching back
// to real timers -- Date.now() reverts to actual wall-clock time at that
// point, which would make the elapsed-time check in db.js think a stage just
// started.
function completedRun(filename) {
  vi.useFakeTimers();
  const created = createRun(filename);
  vi.advanceTimersByTime(3000 * 2 + 1); // Ingesting + Profiling
  const awaiting = findRun(created.id);

  const columns = runProfile(awaiting).columns.filter((c) => c.isCde).map((c) => c.name);
  applyCdeOverride(awaiting, columns);
  vi.advanceTimersByTime(3000 * 2 + 1); // Evaluating + Scoring
  const run = findRun(created.id);
  vi.useRealTimers();
  return run;
}

describe('buildReportPdf', () => {
  it('produces bytes that parse back as a valid one-page PDF for a summary', async () => {
    const run = completedRun('summary-report.csv');
    const bytes = await buildReportPdf(run, 'summary');

    // Real magic-number check -- this is what tells a PDF viewer it's a PDF at
    // all, and it's exactly what the old placeholder implementation faked
    // without actually producing a parseable document behind it.
    expect(Buffer.from(bytes.slice(0, 5)).toString('ascii')).toBe('%PDF-');

    const parsed = await PDFDocument.load(bytes);
    expect(parsed.getPageCount()).toBe(1);
    expect(parsed.getTitle()).toContain(run.file);
  });

  it('produces one overview page plus one page per dimension for in-depth', async () => {
    const run = completedRun('in-depth-report.csv');
    const bytes = await buildReportPdf(run, 'in-depth');

    const parsed = await PDFDocument.load(bytes);
    expect(parsed.getPageCount()).toBe(1 + Object.keys(run.scores).length);
  });
});

describe('buildReportPdf with a not-assessed dimension', () => {
  it('still parses and does not crash on a null dimension score', async () => {
    const run = completedRun('nodate-in-report.csv');
    expect(run.scores.timeliness).toBeNull();

    const bytes = await buildReportPdf(run, 'in-depth');
    const parsed = await PDFDocument.load(bytes);
    // Overview page + one page per dimension, same page count as a fully
    // scored run -- the not-assessed dimension still gets a page, just with
    // a reason instead of a rules table.
    expect(parsed.getPageCount()).toBe(1 + Object.keys(run.scores).length);
  });
});
