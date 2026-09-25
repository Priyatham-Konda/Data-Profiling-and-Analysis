import { useCallback, useEffect, useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { STATUS, STAGES } from '@/api/constants';
import { getRun } from '@/api/runs';
import { formatPercent } from '@/lib/format';
import { CompletedView } from './home/CompletedView';
import { AwaitingCdesView } from './home/AwaitingCdesView';
import { DimensionDrawer } from './home/DimensionDrawer';

// The four small "nothing selected / processing / failed" states live here,
// next to the switch that picks between them (MainState, below). Only
// CompletedView is its own file, because it's genuinely bigger -- a header,
// a download menu, and a grid of dimension tiles (one per entry in
// DIMENSIONS -- deliberately not hardcoding a count here, since that's
// exactly what changed when integrity became a 7th dimension).

function EmptyState() {
  return (
    <div className="grid h-full place-items-center px-10 py-20 text-center">
      <div>
        <p className="text-sm font-semibold text-ink">Select a run to see its report</p>
        <p className="mt-1 text-sm text-ink-3">Pick any assessment from the list on the left.</p>
      </div>
    </div>
  );
}

function ProcessingView({ run }) {
  const progress = run.progress ?? 0;
  const stageIndex = run.stageIndex ?? STAGES.indexOf(run.stage);

  return (
    <div className="mx-auto max-w-2xl px-10 py-14">
      <p className="font-mono text-[13px] text-ink-2">{run.file}</p>
      <h1 className="mt-2 text-2xl font-extrabold tracking-tight">
        {run.stage}
        <span className="text-ink-3">&hellip;</span>
      </h1>

      <div className="mt-6">
        <div
          className="h-1.5 w-full overflow-hidden rounded-full bg-border"
          role="progressbar"
          aria-valuenow={Math.round(progress * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`Assessment progress: ${run.stage}`}
        >
          <div
            className="h-full rounded-full bg-accent transition-[width] duration-700 ease-out"
            style={{ width: `${Math.max(4, progress * 100)}%` }}
          />
        </div>
        <div className="mt-2 flex justify-between text-xs text-ink-3">
          <span>
            Stage {stageIndex + 1} of {run.stageCount ?? STAGES.length}
          </span>
          <span className="font-mono">{formatPercent(progress)}</span>
        </div>
      </div>

      <p className="mt-8 rounded-[var(--radius-card)] border border-border bg-surface px-5 py-4 text-sm text-ink-2">
        This keeps running on its own. It&rsquo;s safe to leave this page, start
        another assessment, or close the tab &mdash; come back whenever to see
        what&rsquo;s next.
      </p>
    </div>
  );
}

function FailedView({ run }) {
  return (
    <div className="mx-auto max-w-2xl px-10 py-14">
      <p className="font-mono text-[13px] text-ink-2">{run.file}</p>
      <h1 className="mt-2 flex items-center gap-2.5 text-2xl font-extrabold tracking-tight">
        <span aria-hidden="true" className="size-2.5 rounded-full bg-critical" />
        Assessment failed
      </h1>

      <div className="mt-6 rounded-[var(--radius-card)] border border-critical/25 bg-critical/6 px-5 py-4">
        <p className="text-xs font-semibold tracking-[0.06em] text-critical uppercase">Error</p>
        <p className="mt-1.5 text-sm leading-relaxed text-ink-2">{run.error}</p>
      </div>

      <Link
        to="/upload"
        className="mt-6 inline-block rounded-[var(--radius-control)] bg-accent px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-accent-hover"
      >
        Upload a corrected file
      </Link>
    </div>
  );
}

export function HomePanel() {
  const [searchParams] = useSearchParams();
  const selectedRunId = searchParams.get('run');

  // Drawer state is local UI state, not navigation -- closing it just returns to
  // whatever Home state was showing underneath.
  const [selectedDimensionKey, setSelectedDimensionKey] = useState(null);

  // Switching runs must not leave the previous run's drawer open. Adjusting
  // during render rather than in an effect: React discards this render and
  // restarts immediately, so the stale drawer is never painted.
  const [lastRunId, setLastRunId] = useState(selectedRunId);
  if (lastRunId !== selectedRunId) {
    setLastRunId(selectedRunId);
    setSelectedDimensionKey(null);
  }

  // The run id is stored alongside its data. That pairing is what lets render
  // tell "loading the new run" apart from "showing the old one", with no
  // separate loading flag to keep in sync.
  const [detail, setDetail] = useState({ runId: null, data: null, error: null });

  useEffect(() => {
    if (!selectedRunId) return undefined;

    // If the user switches runs mid-flight, the first response can land after
    // the second. This flag makes the abandoned request a no-op.
    let cancelled = false;

    async function load() {
      try {
        const data = await getRun(selectedRunId);
        if (!cancelled) setDetail({ runId: selectedRunId, data, error: null });
      } catch (error) {
        if (!cancelled) setDetail({ runId: selectedRunId, data: null, error });
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [selectedRunId]);

  const isCurrent = detail.runId === selectedRunId;
  const run = isCurrent ? detail.data : null;
  const error = isCurrent ? detail.error : null;

  // A manual, one-shot refetch for when something OTHER than the poll needs
  // fresh detail right away -- a CDE override puts the run back into
  // 'processing' immediately, and waiting up to 3s for the next scheduled
  // poll to notice would leave the completed view on screen looking stale.
  // Deliberately not guarded by the `cancelled` flag the mount effect above
  // uses: this only ever runs in response to a user action that already
  // knows which run it's refreshing, so there's no race to guard against.
  const refetchDetail = useCallback(async () => {
    if (!selectedRunId) return;
    try {
      const data = await getRun(selectedRunId);
      setDetail({ runId: selectedRunId, data, error: null });
    } catch (fetchError) {
      setDetail({ runId: selectedRunId, data: null, error: fetchError });
    }
  }, [selectedRunId]);

  // Poll only while this run is both selected and processing. The interval is
  // rebuilt when either changes, and cleared the moment the run finishes.
  useEffect(() => {
    if (!selectedRunId || run?.status !== STATUS.PROCESSING) return undefined;

    const timer = setInterval(async () => {
      try {
        const data = await getRun(selectedRunId);
        setDetail({ runId: selectedRunId, data, error: null });
      } catch {
        // Keep the last good detail on a blip; the next tick may recover.
      }
    }, 3000);

    return () => clearInterval(timer);
  }, [selectedRunId, run?.status]);

  return (
    <>
      <MainState
        selectedRunId={selectedRunId}
        run={run}
        isPending={Boolean(selectedRunId) && !isCurrent}
        isError={Boolean(error)}
        error={error}
        onOpenDimension={setSelectedDimensionKey}
        onReassessed={refetchDetail}
      />

      {selectedDimensionKey && run?.status === STATUS.COMPLETED && (
        <DimensionDrawer
          runId={run.id}
          dimensionKey={selectedDimensionKey}
          onClose={() => setSelectedDimensionKey(null)}
        />
      )}
    </>
  );
}

// A pure function of (selectedRunId, run.status). Nothing here is time-based or
// route-based, which is what keeps Home from ever becoming a waiting screen.
export function MainState({
  selectedRunId,
  run,
  isPending,
  isError,
  error,
  onOpenDimension,
  onReassessed,
}) {
  if (!selectedRunId) return <EmptyState />;

  if (isPending) {
    return <p className="px-10 py-14 text-sm text-ink-3">Loading run&hellip;</p>;
  }

  if (isError) {
    return (
      <p className="px-10 py-14 text-sm text-critical">
        {error?.message ?? 'Could not load this run.'}
      </p>
    );
  }

  switch (run.status) {
    case STATUS.PROCESSING:
      return <ProcessingView run={run} />;
    case STATUS.AWAITING_CDES:
      return <AwaitingCdesView run={run} onConfirmed={onReassessed} />;
    case STATUS.FAILED:
      return <FailedView run={run} />;
    case STATUS.COMPLETED:
      return (
        <CompletedView run={run} onOpenDimension={onOpenDimension} onReassessed={onReassessed} />
      );
    default:
      return <EmptyState />;
  }
}
