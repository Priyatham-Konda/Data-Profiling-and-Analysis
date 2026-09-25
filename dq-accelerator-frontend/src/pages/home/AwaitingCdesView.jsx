import { useContext, useEffect, useState } from 'react';
import { getRunProfile, overrideCdes } from '@/api/runs';
import { RunsContext } from '@/components/RunsProvider';
import { ToastContext } from '@/components/Toast';
import { formatCount } from '@/lib/format';
import { ColumnChecklist } from './ColumnChecklist';

/**
 * The required stop between Profiling and Evaluating/Scoring: the engine has
 * profiled the file and auto-detected a CDE set, and nothing gets scored
 * until a human reviews and confirms it. See db.js's two-phase lifecycle and
 * API_CONTRACT.md.
 *
 * This is a plain HomePanel main-panel state, not an overlay -- unlike
 * ColumnProfilePanel's optional later re-review of a completed run, this is
 * a required step in the primary flow, so it gets the same visual weight as
 * ProcessingView/FailedView rather than sitting on top of them. The checkbox
 * table itself is shared with ColumnProfilePanel via ColumnChecklist.
 */
export function AwaitingCdesView({ run, onConfirmed }) {
  const { refreshRuns } = useContext(RunsContext);
  const { showToast } = useContext(ToastContext);

  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [isSaving, setIsSaving] = useState(false);

  // Which columns are currently checked. Seeded from the detected CDEs once
  // the profile loads, then edited freely by the user.
  const [selected, setSelected] = useState(() => new Set());
  // Adjusting during render rather than in an effect: the moment `data`
  // arrives, React discards this render and restarts with `selected` already
  // seeded, so there's no frame where the checkboxes show empty/stale state.
  const [lastData, setLastData] = useState(null);
  if (data && data !== lastData) {
    setLastData(data);
    setSelected(new Set(data.columns.filter((column) => column.isCde).map((column) => column.name)));
  }

  // Lazy fetch, same as ColumnProfilePanel: this only exists because the run
  // reached awaiting_cdes, not preloaded alongside the rest of the run.
  useEffect(() => {
    let cancelled = false;

    getRunProfile(run.id).then(
      (result) => {
        if (!cancelled) setData(result);
      },
      (err) => {
        if (!cancelled) setError(err);
      },
    );

    return () => {
      cancelled = true;
    };
  }, [run.id]);

  function toggleColumn(name) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  // Deliberately light validation here -- "at least one column" is the only
  // thing checked client-side. Everything else is the backend's call; its
  // error message surfaces through the toast rather than being pre-empted.
  async function handleConfirm() {
    setIsSaving(true);
    try {
      await overrideCdes(run.id, [...selected]);
      showToast(`${run.file} confirmed — scoring now.`);
      // Two different things need to notice the run is processing again: the
      // sidebar's own list (refreshRuns) and this run's own detail poll
      // (onConfirmed, an immediate refetch rather than waiting on its timer).
      refreshRuns();
      onConfirmed?.();
    } catch (err) {
      showToast(`Could not start scoring. ${err.message}`, 'error');
    } finally {
      setIsSaving(false);
    }
  }

  const isPending = !data && !error;

  return (
    <div className="px-10 py-9">
      <p className="font-mono text-[13px] text-ink-2">{run.file}</p>
      <h1 className="mt-2 text-2xl font-extrabold tracking-tight">
        Review critical data elements
      </h1>
      <p className="mt-2 max-w-2xl text-sm text-ink-2">
        We profiled {formatCount(run.columns)} columns and detected{' '}
        {formatCount(run.cdes)} as critical data elements &mdash; the fields carrying
        business meaning. Adjust the selection below if needed, then confirm to start scoring.
      </p>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        {data && (
          <p className="text-xs text-ink-3">
            {selected.size} of {data.columns.length} columns selected
          </p>
        )}
        <button
          type="button"
          onClick={handleConfirm}
          disabled={selected.size === 0 || isSaving || isPending}
          className="rounded-[var(--radius-control)] bg-accent px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isSaving ? 'Starting…' : 'Confirm and start scoring'}
        </button>
      </div>

      <div className="mt-5">
        {isPending && <p className="text-sm text-ink-3">Loading detected columns&hellip;</p>}

        {error && (
          <p className="text-sm text-critical">
            {error?.message ?? 'Could not load the column profile.'}
          </p>
        )}

        {data && (
          <ColumnChecklist columns={data.columns} selected={selected} onToggle={toggleColumn} />
        )}
      </div>
    </div>
  );
}
