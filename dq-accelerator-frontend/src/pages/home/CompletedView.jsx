import { useContext, useState } from 'react';
import { bandPill, bandText, bandLabel } from '@/lib/band';
import { formatCount, formatScore } from '@/lib/format';
import { downloadReport, reportUrl } from '@/api/runs';
import { ToastContext } from '@/components/Toast';
import { DimensionGrid } from './DimensionGrid';
import { ColumnProfilePanel } from './ColumnProfilePanel';

// ScoreHeader and DownloadMenu are only ever rendered together, inside
// CompletedView, so they live in this one file rather than three.

function ScoreHeader({ run, onViewProfile, children }) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-6 border-b border-border pb-7">
      <div className="min-w-0">
        <h1 className="truncate font-mono text-lg font-semibold" title={run.file}>
          {run.file}
        </h1>
        <p className="mt-1 font-mono text-xs text-ink-3">{run.id}</p>

        <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2 text-sm">
          <div>
            <dt className="text-xs text-ink-3">Records</dt>
            <dd className="mt-0.5 font-mono text-ink">{formatCount(run.records)}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-3">CDEs</dt>
            <dd className="mt-0.5 flex items-center gap-1.5 font-mono text-ink">
              {formatCount(run.cdes)}
              {typeof run.columns === 'number' && (
                <span className="text-ink-3">of {formatCount(run.columns)}</span>
              )}
              {run.cdeOverridden && (
                <span className="rounded-full bg-accent/10 px-1.5 py-0.5 font-sans text-[10px] font-semibold text-accent">
                  Adjusted
                </span>
              )}
            </dd>
          </div>
          {run.parseWarnings > 0 && (
            <div>
              <dt className="text-xs text-ink-3">Parse warnings</dt>
              <dd className="mt-0.5 font-mono text-warn">{formatCount(run.parseWarnings)}</dd>
            </div>
          )}
        </dl>

        <button
          type="button"
          onClick={onViewProfile}
          className="mt-3 text-xs font-semibold text-accent transition hover:text-accent-hover"
        >
          View column profile &rsaquo;
        </button>
      </div>

      <div className="flex items-start gap-6">
        <div className="text-right">
          <p className="text-xs text-ink-3">Overall score</p>
          <p className={`font-mono text-4xl font-semibold ${bandText(run.overall)}`}>
            {formatScore(run.overall)}
          </p>
          <span
            className={`mt-1.5 inline-block rounded-full px-2.5 py-1 text-[11px] font-semibold ${bandPill(
              run.overall,
            )}`}
          >
            {bandLabel(run.overall)}
          </span>
        </div>
        {children}
      </div>
    </header>
  );
}

// Plain links, so a real backend's Content-Disposition is handled by the
// browser natively -- no memory copy, a real download progress UI. In dev
// only, the click is intercepted and re-issued as a genuine fetch() instead:
// the MSW mock is a Service Worker, and a Service Worker doesn't reliably see
// requests made by clicking an <a download>, so left alone those clicks fall
// through to Vite's dev server and download its index.html shell instead of
// the mocked report. This branch never runs against the real backend.
function DownloadMenu({ runId }) {
  const { showToast } = useContext(ToastContext);
  const [pendingType, setPendingType] = useState(null);

  async function handleClick(type, event) {
    if (!import.meta.env.DEV) return;
    event.preventDefault();

    setPendingType(type);
    try {
      await downloadReport(runId, type);
    } catch (error) {
      showToast(`Could not download the report. ${error.message}`, 'error');
    } finally {
      setPendingType(null);
    }
  }

  const base =
    'block rounded-[var(--radius-control)] px-4 py-2.5 text-sm font-semibold transition text-center disabled:cursor-wait';

  return (
    <div className="flex flex-col gap-2">
      <a
        href={reportUrl(runId, 'summary')}
        download
        aria-disabled={pendingType === 'summary'}
        onClick={(event) => handleClick('summary', event)}
        className={`${base} bg-accent text-white hover:bg-accent-hover`}
      >
        {pendingType === 'summary' ? 'Downloading…' : 'Download summary'}
      </a>
      <a
        href={reportUrl(runId, 'in-depth')}
        download
        aria-disabled={pendingType === 'in-depth'}
        onClick={(event) => handleClick('in-depth', event)}
        className={`${base} border border-border bg-surface text-ink-2 hover:bg-ink/4`}
      >
        {pendingType === 'in-depth' ? 'Downloading…' : 'In-depth report'}
      </a>
    </div>
  );
}

export function CompletedView({ run, onOpenDimension, onReassessed }) {
  const [isProfileOpen, setIsProfileOpen] = useState(false);

  return (
    <div className="px-10 py-9">
      <ScoreHeader run={run} onViewProfile={() => setIsProfileOpen(true)}>
        <DownloadMenu runId={run.id} />
      </ScoreHeader>

      {run.sampled && (
        <p className="mt-5 rounded-[var(--radius-control)] border border-border bg-surface px-4 py-3 text-xs leading-relaxed text-ink-2">
          This assessment used a sample of{' '}
          <span className="font-mono">{formatCount(run.sampledRows)}</span> of{' '}
          <span className="font-mono">{formatCount(run.records)}</span> rows. Scores are
          statistically representative, not an exact count.
        </p>
      )}

      <DimensionGrid
        scores={run.scores}
        notAssessed={run.notAssessed}
        onOpenDimension={onOpenDimension}
      />

      {isProfileOpen && (
        <ColumnProfilePanel
          runId={run.id}
          onClose={() => setIsProfileOpen(false)}
          onReassessed={onReassessed}
        />
      )}
    </div>
  );
}
