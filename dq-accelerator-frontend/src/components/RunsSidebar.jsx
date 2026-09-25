import { useContext, useEffect, useRef, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { STATUS } from '@/api/constants';
import { formatScore } from '@/lib/format';
import { bandText } from '@/lib/band';
import { RunsContext } from './RunsProvider';

// This file is the whole left-hand sidebar, built bottom-up:
// StatusDot -> RunListItem -> ConfirmDialog -> RunsSidebar. None of the first
// three are used anywhere else, so they stay private to this file instead of
// each having its own.

// The only place a run status is mapped to a colour. awaiting_cdes is
// deliberately solid accent (not pulsing amber like processing) -- pulsing
// implies "keep watching, it's advancing on its own", which is exactly wrong
// here: nothing happens until the user reviews and confirms.
const STATUS_COLOR = {
  [STATUS.PROCESSING]: 'bg-warn',
  [STATUS.AWAITING_CDES]: 'bg-accent',
  [STATUS.COMPLETED]: 'bg-healthy',
  [STATUS.FAILED]: 'bg-critical',
};

function StatusDot({ status }) {
  const isLive = status === STATUS.PROCESSING;
  return (
    <span className="relative flex size-2 shrink-0">
      {isLive && (
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-warn opacity-60" />
      )}
      <span
        className={`relative inline-flex size-2 rounded-full ${STATUS_COLOR[status] ?? 'bg-ink-3'}`}
      />
    </span>
  );
}

function statusLine(run) {
  if (run.status === STATUS.PROCESSING) return 'Processing…';
  if (run.status === STATUS.AWAITING_CDES) return 'Review CDEs';
  if (run.status === STATUS.FAILED) return 'Failed';
  return `Score ${formatScore(run.overall)}`;
}

function RunListItem({ run, isSelected, onSelect, onRequestDelete }) {
  return (
    // A row, not a single button: nesting the delete button inside the select
    // button would be invalid HTML and unreachable by keyboard.
    <li
      className={`group relative flex items-start rounded-[var(--radius-control)] transition ${
        isSelected ? 'bg-accent/8 ring-1 ring-accent/20' : 'hover:bg-ink/4'
      }`}
    >
      <button
        type="button"
        onClick={() => onSelect(run.id)}
        aria-current={isSelected ? 'true' : undefined}
        className="min-w-0 flex-1 rounded-[var(--radius-control)] px-3 py-2.5 text-left"
      >
        <span className="flex items-center gap-2">
          <StatusDot status={run.status} />
          <span className="truncate font-mono text-[13px] text-ink" title={run.file}>
            {run.file}
          </span>
        </span>
        <span
          className={`mt-1 block pl-4 text-xs ${
            run.status === STATUS.COMPLETED ? bandText(run.overall) : 'text-ink-3'
          }`}
        >
          {statusLine(run)}
        </span>
      </button>

      <button
        type="button"
        onClick={() => onRequestDelete(run)}
        aria-label={`Delete ${run.file}`}
        title="Delete run"
        // Hidden until hover or keyboard focus, so the list stays calm but the
        // action is never keyboard-inaccessible.
        className="mt-2 mr-1.5 shrink-0 rounded px-1.5 py-1 text-ink-3 opacity-0 transition group-hover:opacity-100 hover:bg-critical/10 hover:text-critical focus-visible:opacity-100"
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 16 16"
          className="size-3.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
        >
          <path d="M2.5 4h11M6 4V2.5h4V4M4 4l.6 9a1 1 0 0 0 1 1h4.8a1 1 0 0 0 1-1L12 4M6.5 6.5v5M9.5 6.5v5" />
        </svg>
      </button>
    </li>
  );
}

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Small confirmation for the destructive delete action. Focus starts on
// Cancel, not Delete, so a stray Enter can never delete something.
function ConfirmDialog({ title, body, isPending, onConfirm, onCancel }) {
  const panelRef = useRef(null);
  const cancelRef = useRef(null);
  const openerRef = useRef(null);

  // Modal behaviour: focus in, trap Tab, close on Escape, hand focus back.
  useEffect(() => {
    openerRef.current = document.activeElement;
    cancelRef.current?.focus();

    function handleKeyDown(event) {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onCancel();
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

    document.addEventListener('keydown', handleKeyDown);
    // Without this cleanup a dismissed dialog would keep eating Escape.
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      openerRef.current?.focus?.();
    };
  }, [onCancel]);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4">
      <div
        className="animate-fade-in absolute inset-0 bg-ink/35"
        onClick={onCancel}
        aria-hidden="true"
      />

      <div
        ref={panelRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-body"
        className="animate-fade-in relative w-full max-w-sm rounded-[var(--radius-card)] border border-border bg-surface p-6 shadow-xl shadow-black/10"
      >
        <h2 id="confirm-title" className="text-base font-bold tracking-tight">
          {title}
        </h2>
        <div id="confirm-body" className="mt-2 text-sm leading-relaxed text-ink-2">
          {body}
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            className="rounded-[var(--radius-control)] border border-border bg-surface px-4 py-2 text-sm font-semibold text-ink-2 transition hover:bg-ink/4"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={isPending}
            className="rounded-[var(--radius-control)] bg-critical px-4 py-2 text-sm font-semibold text-white transition hover:brightness-95 disabled:opacity-60"
          >
            {isPending ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  );
}

export function RunsSidebar({ selectedRunId }) {
  const { runs, runsError, isLoadingRuns, removeRun, deletingId } = useContext(RunsContext);
  const [pendingDelete, setPendingDelete] = useState(null);
  const navigate = useNavigate();

  // Selection is a URL search param, so clicking a run is a navigation to Home
  // with ?run=<id>. Nothing else about the shell changes.
  const selectRun = (id) => navigate(`/?run=${encodeURIComponent(id)}`);

  async function confirmDelete() {
    const run = pendingDelete;
    // Clear the selection first, otherwise the detail poll keeps hitting an id
    // that is about to stop existing and Home flips to an error.
    if (run.id === selectedRunId) navigate('/', { replace: true });
    await removeRun(run);
    setPendingDelete(null);
  }

  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex items-center gap-2.5 px-5 py-6">
        <span
          aria-hidden="true"
          className="grid size-7 place-items-center rounded-lg bg-accent font-mono text-[11px] font-bold text-white"
        >
          DQ
        </span>
        <span className="text-sm font-bold tracking-tight">DQ Accelerator</span>
      </div>

      <div className="px-3">
        <NavLink
          to="/upload"
          className={({ isActive }) =>
            `flex items-center gap-2 rounded-[var(--radius-control)] px-3 py-2 text-sm font-semibold transition ${
              isActive ? 'bg-accent text-white' : 'text-accent hover:bg-accent/8'
            }`
          }
        >
          <span aria-hidden="true" className="text-base leading-none">
            +
          </span>
          New assessment
        </NavLink>
      </div>

      <p className="px-5 pt-7 pb-2 text-[11px] font-semibold tracking-[0.08em] text-ink-3 uppercase">
        Runs
      </p>

      <nav aria-label="Assessment runs" className="min-h-0 flex-1 overflow-y-auto px-3 pb-6">
        {isLoadingRuns && <p className="px-3 py-2 text-xs text-ink-3">Loading runs&hellip;</p>}

        {runsError && <p className="px-3 py-2 text-xs text-critical">Could not load runs.</p>}

        {runs?.length === 0 && <p className="px-3 py-2 text-xs text-ink-3">No runs yet.</p>}

        <ul className="space-y-1">
          {runs?.map((run) => (
            <RunListItem
              key={run.id}
              run={run}
              isSelected={run.id === selectedRunId}
              onSelect={selectRun}
              onRequestDelete={setPendingDelete}
            />
          ))}
        </ul>
      </nav>

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this run?"
          body={
            <>
              <span className="font-mono text-[13px] text-ink">{pendingDelete.file}</span>
              {[STATUS.PROCESSING, STATUS.AWAITING_CDES].includes(pendingDelete.status)
                ? ' is still being assessed. Deleting it cancels the assessment.'
                : ' and its report will be removed.'}{' '}
              This can&rsquo;t be undone.
            </>
          }
          isPending={deletingId === pendingDelete.id}
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </aside>
  );
}
