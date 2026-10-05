import { Link } from 'react-router-dom';
import { DIMENSIONS, STATUS } from '@/api/constants';
import {
  assessmentDataUrl,
  assessmentReportUrl,
  downloadAssessmentData,
  downloadAssessmentReport,
} from '@/api/salesforce';
import { bandLabel, bandPill, bandText } from '@/lib/band';
import { formatCount, formatScore } from '@/lib/format';
import { DimensionGrid } from '../home/DimensionGrid';
import { DownloadMenu } from '../home/CompletedView';
import { AssessmentHeading } from '../AssessmentPanel';

/**
 * A completed Salesforce assessment. The overall score is a records-weighted
 * blend of the objects, so it is never shown without the per-object table
 * underneath: one weak object can hide inside a healthy-looking average.
 * Each row opens that object's own run, which has the drilldowns.
 */
export function AssessmentResultsView({ assessment }) {
  const completed = assessment.runs.filter((run) => run.status === STATUS.COMPLETED);
  const failed = assessment.runs.filter((run) => run.status === STATUS.FAILED);

  return (
    <div className="px-10 py-9">
      <header className="flex flex-wrap items-start justify-between gap-6 border-b border-border pb-7">
        <div className="min-w-0">
          <AssessmentHeading assessment={assessment} />
          <p className="mt-1 font-mono text-xs text-ink-3">{assessment.id}</p>
          <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2 text-sm">
            <div>
              <dt className="text-xs text-ink-3">Records</dt>
              <dd className="mt-0.5 font-mono text-ink">{formatCount(assessment.records)}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-3">Objects</dt>
              <dd className="mt-0.5 font-mono text-ink">{formatCount(assessment.objects)}</dd>
            </div>
          </dl>
        </div>

        <div className="flex items-start gap-6">
          <div className="text-right">
            <p className="text-xs text-ink-3">Overall score</p>
            <p className={`font-mono text-4xl font-semibold ${bandText(assessment.overall)}`}>
              {formatScore(assessment.overall)}
            </p>
            <span
              className={`mt-1.5 inline-block rounded-full px-2.5 py-1 text-[11px] font-semibold ${bandPill(
                assessment.overall,
              )}`}
            >
              {bandLabel(assessment.overall)}
            </span>
          </div>
          <DownloadMenu
            hrefFor={(type) => assessmentReportUrl(assessment.id, type)}
            download={(type) => downloadAssessmentReport(assessment.id, type)}
            data={{
              href: assessmentDataUrl(assessment.id),
              download: () => downloadAssessmentData(assessment.id),
              label: 'Download all data (ZIP)',
            }}
          />
        </div>
      </header>

      {assessment.partial && (
        <p className="mt-5 rounded-[var(--radius-control)] border border-warn/30 bg-warn/8 px-4 py-3 text-xs leading-relaxed text-ink-2">
          Overall covers {completed.length} of {assessment.runs.length} objects.{' '}
          {failed.map((run) => run.label).join(', ')} could not be assessed.
        </p>
      )}

      <DimensionGrid
        scores={assessment.scores}
        notAssessed={assessment.notAssessed}
        interactive={false}
      />

      <section className="mt-9">
        <h2 className="text-sm font-semibold text-ink">By object</h2>
        <p className="mt-1 text-xs text-ink-3">Open an object for its rules and failing records.</p>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-ink-3">
                <th scope="col" className="pb-2 pr-6 font-medium">
                  Object
                </th>
                <th scope="col" className="pb-2 pr-6 font-medium">
                  Records
                </th>
                <th scope="col" className="pb-2 pr-6 font-medium">
                  Overall
                </th>
                {DIMENSIONS.map((dimension) => (
                  <th key={dimension.key} scope="col" className="pb-2 pr-4 font-medium">
                    {dimension.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {assessment.runs.map((run) => (
                <tr key={run.id} className="border-b border-border/70 align-top">
                  <td className="py-3 pr-6">
                    <Link
                      to={`/?run=${encodeURIComponent(run.id)}`}
                      className="font-semibold text-accent transition hover:text-accent-hover"
                    >
                      {run.label}
                    </Link>
                    <p className="font-mono text-[11px] text-ink-3">{run.object}</p>
                  </td>
                  {run.status === STATUS.COMPLETED ? (
                    <>
                      <td className="py-3 pr-6 font-mono text-ink-2">{formatCount(run.records)}</td>
                      <td className={`py-3 pr-6 font-mono font-semibold ${bandText(run.overall)}`}>
                        {formatScore(run.overall)}
                      </td>
                      {DIMENSIONS.map((dimension) => {
                        const score = run.scores?.[dimension.key];
                        return (
                          <td
                            key={dimension.key}
                            className={`py-3 pr-4 font-mono ${bandText(score)}`}
                            title={score == null ? run.notAssessed?.[dimension.key] : undefined}
                          >
                            {score == null ? '–' : formatScore(score)}
                          </td>
                        );
                      })}
                    </>
                  ) : (
                    <td colSpan={DIMENSIONS.length + 2} className="py-3 text-xs text-critical">
                      {run.error ?? 'Not assessed.'}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
