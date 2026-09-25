import { useContext, useEffect, useRef, useState } from 'react';
import { getRunProfile, overrideCdes } from '@/api/runs';
import { RunsContext } from '@/components/RunsProvider';
import { ToastContext } from '@/components/Toast';
import { ColumnChecklist } from './ColumnChecklist';

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"]), input:not([disabled])';

/**
 * GET /runs/{id}/profile plus PUT /runs/{id}/cdes: every column the engine
 * saw and why it was picked as a CDE, with a checkbox per column so that
 * selection can be corrected and re-assessed. CDE selection drives every
 * score in the report, so this exists to make that choice both inspectable
 * and fixable rather than a black box.
 *
 * Full-screen, opened from ScoreHeader. Escape/Tab are handled with a plain
 * onKeyDown on this panel rather than a document listener -- the same
 * reasoning as RuleExamplesOverlay in DimensionDrawer.jsx: focus is trapped
 * inside this panel, so the keydown always bubbles up through it first, and
 * stopPropagation() here means it can't also trigger anything listening on
 * document underneath.
 */
export function ColumnProfilePanel({ runId, onClose, onReassessed }) {
  const { refreshRuns } = useContext(RunsContext);
  const { showToast } = useContext(ToastContext);

  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [isSaving, setIsSaving] = useState(false);
  const panelRef = useRef(null);
  const closeRef = useRef(null);
  const openerRef = useRef(null);

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

  // Lazy fetch: this request only exists because the panel was opened.
  useEffect(() => {
    let cancelled = false;

    getRunProfile(runId).then(
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
  }, [runId]);

  useEffect(() => {
    openerRef.current = document.activeElement;
    closeRef.current?.focus();
    return () => openerRef.current?.focus?.();
  }, []);

  function handleKeyDown(event) {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;

    const items = panelRef.current?.querySelectorAll(FOCUSABLE);
    if (!items?.length) return;
    const first = items[0];
    const last = items[items.length - 1];

    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function toggleColumn(name) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  // Deliberately light validation here -- "at least one column" is the only
  // thing checked client-side. Everything else (an unknown column name, the
  // run no longer being completed) is the backend's call; its error message
  // surfaces through the toast below rather than being pre-empted here.
  async function handleReassess() {
    setIsSaving(true);
    try {
      await overrideCdes(runId, [...selected]);
      showToast('Columns updated — re-assessing with the new selection.');
      // Two different things need to notice the run is processing again: the
      // sidebar's own list (refreshRuns) and this run's own detail poll
      // (onReassessed, an immediate refetch rather than waiting on its timer).
      refreshRuns();
      onReassessed?.();
      onClose();
    } catch (err) {
      showToast(`Could not update columns. ${err.message}`, 'error');
    } finally {
      setIsSaving(false);
    }
  }

  const isPending = !data && !error;

  return (
    <div
      ref={panelRef}
      role="dialog"
      aria-modal="true"
      aria-label="Column profile"
      onKeyDown={handleKeyDown}
      className="animate-fade-in fixed inset-0 z-[60] flex flex-col bg-surface"
    >
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-border px-6 py-5 md:px-10">
        <div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="flex items-center gap-1.5 text-sm font-semibold text-accent transition hover:text-accent-hover"
          >
            <span aria-hidden="true">&larr;</span>
            Back to report
          </button>
          <h2 className="mt-3 text-lg font-bold tracking-tight">Column profile</h2>
          {data && (
            <p className="mt-0.5 text-xs text-ink-3">
              {selected.size} of {data.columns.length} columns selected as critical data
              elements
            </p>
          )}
        </div>

        {data && (
          <button
            type="button"
            onClick={handleReassess}
            disabled={selected.size === 0 || isSaving}
            className="rounded-[var(--radius-control)] bg-accent px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isSaving ? 'Re-assessing…' : 'Re-assess with this selection'}
          </button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6 md:px-10">
        {isPending && <p className="text-sm text-ink-3">Loading column profile&hellip;</p>}

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
