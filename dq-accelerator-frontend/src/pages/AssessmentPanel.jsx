import { useCallback, useEffect, useState } from 'react';
import { STATUS } from '@/api/constants';
import { getAssessment } from '@/api/salesforce';
import { formatPercent } from '@/lib/format';
import { AssessmentConfirmView } from './assessment/AssessmentConfirmView';
import { AssessmentResultsView } from './assessment/AssessmentResultsView';

// Revision 5: the main panel when a Salesforce assessment (?assessment=id) is
// selected. Same shape as HomePanel's run detail: the id is stored alongside
// its data, a 3s poll runs only while processing, and a pure switch on status
// picks the view.

function SandboxBadge({ source }) {
  if (source?.environment !== 'sandbox') return null;
  return (
    <span className="rounded-full bg-warn/12 px-2 py-0.5 text-[10px] font-semibold text-warn">
      Sandbox
    </span>
  );
}

export function AssessmentHeading({ assessment, children }) {
  return (
    <div>
      <p className="flex items-center gap-2 text-[13px] text-ink-2">
        Salesforce <SandboxBadge source={assessment.source} />
      </p>
      <h1 className="mt-2 text-2xl font-extrabold tracking-tight">{children ?? assessment.name}</h1>
    </div>
  );
}

function ProcessingView({ assessment }) {
  const progress = assessment.progress ?? 0;
  return (
    <div className="mx-auto max-w-2xl px-10 py-14">
      <AssessmentHeading assessment={assessment} />

      <div className="mt-6">
        <div
          className="h-1.5 w-full overflow-hidden rounded-full bg-border"
          role="progressbar"
          aria-valuenow={Math.round(progress * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Assessment progress"
        >
          <div
            className="h-full rounded-full bg-accent transition-[width] duration-700 ease-out"
            style={{ width: `${Math.max(4, progress * 100)}%` }}
          />
        </div>
        <p className="mt-2 text-right font-mono text-xs text-ink-3">{formatPercent(progress)}</p>
      </div>

      <ul className="mt-6 divide-y divide-border rounded-[var(--radius-card)] border border-border bg-surface">
        {assessment.runs.map((run) => (
          <li key={run.id} className="flex items-start justify-between gap-4 px-5 py-3 text-sm">
            <div className="min-w-0">
              <p className="text-ink">{run.label}</p>
              <p className="font-mono text-[11px] text-ink-3">{run.object}</p>
            </div>
            <div className="text-right text-xs text-ink-2">
              <p>{objectStatusText(run)}</p>
              {run.stageDetail && <p className="mt-0.5 text-ink-3">{run.stageDetail}</p>}
            </div>
          </li>
        ))}
      </ul>

      <p className="mt-8 rounded-[var(--radius-card)] border border-border bg-surface px-5 py-4 text-sm text-ink-2">
        This keeps running on its own. It&rsquo;s safe to leave this page or close the tab.
        Once every object is profiled you&rsquo;ll be asked to confirm their critical data
        elements.
      </p>
    </div>
  );
}

function objectStatusText(run) {
  switch (run.status) {
    case STATUS.PROCESSING:
      return `${run.stage}…`;
    case STATUS.AWAITING_CDES:
      return 'Profiled, waiting for the rest';
    case STATUS.COMPLETED:
      return 'Scored';
    case STATUS.FAILED:
      return 'Failed';
    default:
      return run.status;
  }
}

function FailedView({ assessment }) {
  return (
    <div className="mx-auto max-w-2xl px-10 py-14">
      <AssessmentHeading assessment={assessment}>
        <span className="flex items-center gap-2.5">
          <span aria-hidden="true" className="size-2.5 rounded-full bg-critical" />
          {assessment.name}: assessment failed
        </span>
      </AssessmentHeading>
      <p className="mt-2 text-sm text-ink-2">No selected object could be assessed.</p>

      <ul className="mt-6 space-y-3">
        {assessment.runs.map((run) => (
          <li
            key={run.id}
            className="rounded-[var(--radius-card)] border border-critical/25 bg-critical/6 px-5 py-4"
          >
            <p className="text-sm font-semibold text-ink">
              {run.label} <span className="font-mono text-[11px] text-ink-3">{run.object}</span>
            </p>
            <p className="mt-1 text-sm leading-relaxed text-ink-2">{run.error}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AssessmentPanel({ assessmentId }) {
  const [detail, setDetail] = useState({ assessmentId: null, data: null, error: null });

  useEffect(() => {
    let cancelled = false;
    getAssessment(assessmentId).then(
      (data) => {
        if (!cancelled) setDetail({ assessmentId, data, error: null });
      },
      (error) => {
        if (!cancelled) setDetail({ assessmentId, data: null, error });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [assessmentId]);

  const isCurrent = detail.assessmentId === assessmentId;
  const assessment = isCurrent ? detail.data : null;
  const error = isCurrent ? detail.error : null;

  // After confirming CDEs the assessment is processing again straight away;
  // refetch now rather than waiting for a poll that isn't running yet.
  const refetch = useCallback(async () => {
    try {
      const data = await getAssessment(assessmentId);
      setDetail({ assessmentId, data, error: null });
    } catch (fetchError) {
      setDetail({ assessmentId, data: null, error: fetchError });
    }
  }, [assessmentId]);

  useEffect(() => {
    if (assessment?.status !== STATUS.PROCESSING) return undefined;
    const timer = setInterval(async () => {
      try {
        const data = await getAssessment(assessmentId);
        setDetail({ assessmentId, data, error: null });
      } catch {
        // Keep the last good detail on a blip; the next tick may recover.
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [assessmentId, assessment?.status]);

  if (error) {
    return (
      <p className="px-10 py-14 text-sm text-critical">
        {error.message ?? 'Could not load this assessment.'}
      </p>
    );
  }
  if (!assessment) {
    return <p className="px-10 py-14 text-sm text-ink-3">Loading assessment&hellip;</p>;
  }

  switch (assessment.status) {
    case STATUS.PROCESSING:
      return <ProcessingView assessment={assessment} />;
    case STATUS.AWAITING_CDES:
      return <AssessmentConfirmView assessment={assessment} onConfirmed={refetch} />;
    case STATUS.COMPLETED:
      return <AssessmentResultsView assessment={assessment} />;
    default:
      return <FailedView assessment={assessment} />;
  }
}
