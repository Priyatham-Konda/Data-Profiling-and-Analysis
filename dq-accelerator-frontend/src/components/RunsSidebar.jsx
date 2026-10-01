import { useContext, useEffect, useRef, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { STATUS } from '@/api/constants';
import { getAssessment } from '@/api/salesforce';
import { formatScore } from '@/lib/format';
import { bandText } from '@/lib/band';
import { RunsContext } from './RunsProvider';
import logoUrl from '@/assets/dataskate-logo.png';

// This file is the whole left-hand sidebar, built bottom-up:
// StatusDot -> Badge -> RunListItem -> AssessmentObjects -> SidebarSection ->
// ConfirmDialog -> RunsSidebar. None of them are used anywhere else, so they
// stay private to this file instead of each having its own.

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

function statusLine(item) {
  if (item.status === STATUS.PROCESSING) return 'Processing…';
  if (item.status === STATUS.AWAITING_CDES) return 'Review CDEs';
  if (item.status === STATUS.FAILED) return 'Failed';
  const objects = item.objects ? ` \u00b7 ${item.objects} objects` : '';
  return `Score ${formatScore(item.overall)}${objects}`;
}

const BADGE_TONE = {
  salesforce: 'bg-[#0176d3]/10 text-[#0176d3]',
  sandbox: 'bg-warn/12 text-warn',
};

function Badge({ tone, children, title }) {
  return (
    <span
      title={title}
      className={`shrink-0 rounded-full px-1.5 py-px font-sans text-[9px] font-bold tracking-wide uppercase ${BADGE_TONE[tone]}`}
    >
      {children}
    </span>
  );
}

function Chevron({ open }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 16"
      className={`size-3 transition-transform ${open ? 'rotate-90' : ''}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M6 4l4 4-4 4" />
    </svg>
  );
}

// One row, used for both runs (label = file name) and revision-5 Salesforce
// assessments (label = org name). A run waiting on a CDE review gets a tinted
// row and a bold "Review CDEs" line, since nothing moves until someone acts;
// `badges` say where a run came from. `expander` is an assessment's
// show-objects toggle, `children` its expanded object list.
// In select mode (bulk delete) the row shows a checkbox and clicking it ticks
// the row rather than opening it; `checkDisabled` is the reason a row can't be
// ticked, shown as its tooltip.
function RunListItem({
  run,
  label,
  isSelected,
  onSelect,
  onRequestDelete,
  badges,
  expander,
  children,
  selectMode = false,
  isChecked = false,
  onToggleCheck,
  checkDisabled = null,
}) {
  const needsReview = run.status === STATUS.AWAITING_CDES;
  return (
    <li>
      {/* A row, not a single button: nesting the delete button inside the
          select button would be invalid HTML and unreachable by keyboard. */}
      <div
        className={`group relative flex items-start rounded-[var(--radius-control)] transition ${
          // A run waiting on a review stays highlighted whether or not it's
          // selected (the left bar and tint), selection adds a stronger ring.
          needsReview
            ? `bg-accent/12 shadow-[inset_3px_0_0_var(--color-accent)] hover:bg-accent/16 ${
                isSelected ? 'ring-2 ring-accent/60' : 'ring-1 ring-accent/35'
              }`
            : isSelected
              ? 'bg-accent/8 ring-1 ring-accent/20'
              : 'hover:bg-ink/4'
        }`}
      >
        {selectMode ? (
          <input
            type="checkbox"
            checked={isChecked}
            disabled={Boolean(checkDisabled)}
            onChange={() => onToggleCheck(run)}
            title={checkDisabled ?? undefined}
            aria-label={`Select ${label}`}
            className="mt-3 ml-2.5 size-3.5 shrink-0 accent-critical disabled:opacity-40"
          />
        ) : (
          expander
        )}
        <button
          type="button"
          onClick={() => {
            if (!selectMode) onSelect(run.id);
            else if (!checkDisabled) onToggleCheck(run);
          }}
          aria-current={isSelected ? 'true' : undefined}
          title={selectMode ? (checkDisabled ?? undefined) : undefined}
          className={`min-w-0 flex-1 rounded-[var(--radius-control)] py-2.5 pr-1 text-left ${
            selectMode ? 'pl-2' : expander ? 'pl-0.5' : 'pl-3'
          } ${selectMode && checkDisabled ? 'cursor-not-allowed opacity-60' : ''}`}
        >
          <span className="flex items-center gap-2">
            <StatusDot status={run.status} />
            <span className="truncate font-mono text-[13px] text-ink" title={label}>
              {label}
            </span>
          </span>
          <span className="mt-1 flex flex-wrap items-center gap-1.5 pl-4">
            <span
              className={`text-xs ${
                run.status === STATUS.COMPLETED
                  ? bandText(run.overall)
                  : needsReview
                    ? 'font-semibold text-accent'
                    : 'text-ink-3'
              }`}
            >
              {statusLine(run)}
            </span>
            {badges}
          </span>
        </button>

        {!selectMode && (
          <button
            type="button"
            onClick={() => onRequestDelete(run)}
            aria-label={`Delete ${label}`}
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
        )}
      </div>
      {children}
    </li>
  );
}

// An expanded assessment's objects, each opening its own run. Fetched when
// opened, and again whenever the assessment's summary changes, so the rows
// follow the sidebar's own assessment poll.
function AssessmentObjects({ assessment, selectedRunId, onSelectRun }) {
  const [detail, setDetail] = useState({ runs: null, error: null });
  const version = `${assessment.status}:${assessment.overall ?? ''}`;

  useEffect(() => {
    let cancelled = false;
    getAssessment(assessment.id).then(
      (data) => {
        if (!cancelled) setDetail({ runs: data.runs, error: null });
      },
      (error) => {
        if (!cancelled) setDetail({ runs: null, error });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [assessment.id, version]);

  if (detail.error) {
    return <p className="py-1.5 pl-9 text-[11px] text-critical">Could not load objects.</p>;
  }
  // Previous rows stay on screen while a refetch is in flight.
  if (!detail.runs) {
    return <p className="py-1.5 pl-9 text-[11px] text-ink-3">Loading objects&hellip;</p>;
  }

  return (
    <ul className="mt-0.5 mb-1 ml-5 space-y-px border-l border-border pl-2">
      {detail.runs.map((run) => {
        const isSelected = run.id === selectedRunId;
        return (
          <li key={run.id}>
            <button
              type="button"
              onClick={() => onSelectRun(run.id)}
              aria-current={isSelected ? 'true' : undefined}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition ${
                isSelected ? 'bg-accent/8' : 'hover:bg-ink/4'
              }`}
            >
              <StatusDot status={run.status} />
              <span className="min-w-0 flex-1 truncate text-xs text-ink" title={run.object}>
                {run.label}
              </span>
              <span
                className={`shrink-0 font-mono text-[11px] ${
                  run.status === STATUS.COMPLETED ? bandText(run.overall) : 'text-ink-3'
                }`}
              >
                {run.status === STATUS.COMPLETED && formatScore(run.overall)}
                {run.status === STATUS.FAILED && 'Failed'}
                {run.status === STATUS.AWAITING_CDES && 'Review'}
                {run.status === STATUS.PROCESSING && '…'}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

// A collapsible sidebar group. The header shows how many items it holds and,
// even while collapsed, how many are waiting on a CDE review, so a parked run
// can't disappear from view by being folded away.
function SidebarSection({ id, title, count, reviewCount, children }) {
  const [isOpen, setIsOpen] = useState(true);
  return (
    <section className="pt-4 first:pt-0">
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        aria-expanded={isOpen}
        aria-controls={id}
        className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-[11px] font-semibold tracking-[0.08em] text-ink-3 uppercase transition hover:bg-ink/4 hover:text-ink-2"
      >
        <Chevron open={isOpen} />
        {title}
        <span className="rounded-full bg-ink/6 px-1.5 font-mono text-[10px] tracking-normal text-ink-2">
          {count}
        </span>
        {reviewCount > 0 && (
          <span className="ml-auto rounded-full bg-accent px-1.5 py-px text-[10px] font-semibold tracking-normal text-white normal-case">
            {reviewCount} to review
          </span>
        )}
      </button>
      {isOpen && (
        <div id={id} className="mt-1">
          {children}
        </div>
      )}
    </section>
  );
}

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Small confirmation for the destructive delete action. Focus starts on
// Cancel, not Delete, so a stray Enter can never delete something.
function ConfirmDialog({ title, body, isPending, onConfirm, onCancel, confirmLabel = 'Delete' }) {
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
            {isPending ? 'Deleting…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export function RunsSidebar({ selectedRunId, selectedAssessmentId }) {
  const {
    runs,
    runsError,
    isLoadingRuns,
    removeRun,
    deletingId,
    assessments,
    removeAssessment,
    removeMany,
    isBulkDeleting,
  } = useContext(RunsContext);
  // Bulk delete: select mode shows a checkbox per row. `checked` holds
  // "run:<id>" / "assessment:<id>" keys, so runs and assessments can be
  // ticked together without their ids colliding.
  const [selectMode, setSelectMode] = useState(false);
  const [checked, setChecked] = useState(() => new Set());
  const [isBulkConfirmOpen, setIsBulkConfirmOpen] = useState(false);
  // { kind: 'run' | 'assessment', item }
  const [pendingDelete, setPendingDelete] = useState(null);
  // Which assessments have their object list expanded.
  const [expanded, setExpanded] = useState(() => new Set());

  function toggleExpanded(id) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const countReview = (items) =>
    items?.filter((item) => item.status === STATUS.AWAITING_CDES).length ?? 0;

  // An object run is deleted with its assessment, never alone (the backend
  // answers 409), so it can't be ticked.
  const checkDisabledFor = (run) =>
    run.assessmentId ? 'Part of a Salesforce assessment. Delete the assessment instead.' : null;

  // Derived at render from the live lists, so an item deleted elsewhere (or
  // by the last bulk delete) silently drops out of the selection.
  const checkedRuns = (runs ?? []).filter((run) => checked.has(`run:${run.id}`));
  const checkedAssessments = (assessments ?? []).filter((a) => checked.has(`assessment:${a.id}`));
  const checkedCount = checkedRuns.length + checkedAssessments.length;
  const selectableKeys = [
    ...(runs ?? []).filter((run) => !checkDisabledFor(run)).map((run) => `run:${run.id}`),
    ...(assessments ?? []).map((a) => `assessment:${a.id}`),
  ];
  const allChecked = selectableKeys.length > 0 && checkedCount === selectableKeys.length;

  function toggleCheck(key) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function exitSelectMode() {
    setSelectMode(false);
    setChecked(new Set());
    setIsBulkConfirmOpen(false);
  }

  async function confirmBulkDelete() {
    // Same reason as the single delete: leave a selection that's about to go.
    const clearsRun = checkedRuns.some((run) => run.id === selectedRunId);
    const clearsAssessment = checkedAssessments.some((a) => a.id === selectedAssessmentId);
    if (clearsRun || clearsAssessment) navigate('/', { replace: true });
    await removeMany({ runs: checkedRuns, assessments: checkedAssessments });
    exitSelectMode();
  }

  const checkedNames = [
    ...checkedRuns.map((r) => r.file),
    ...checkedAssessments.map((a) => a.name),
  ];
  const checkedInProgress = [...checkedRuns, ...checkedAssessments].filter((item) =>
    [STATUS.PROCESSING, STATUS.AWAITING_CDES].includes(item.status),
  ).length;
  const navigate = useNavigate();

  // Selection is a URL search param, so clicking a run is a navigation to Home
  // with ?run=<id> (or ?assessment=<id>). Nothing else about the shell changes.
  const selectRun = (id) => navigate(`/?run=${encodeURIComponent(id)}`);
  const selectAssessment = (id) => navigate(`/?assessment=${encodeURIComponent(id)}`);

  async function confirmDelete() {
    const { kind, item } = pendingDelete;
    // Clear the selection first, otherwise the detail poll keeps hitting an id
    // that is about to stop existing and Home flips to an error.
    const selectedId = kind === 'run' ? selectedRunId : selectedAssessmentId;
    if (item.id === selectedId) navigate('/', { replace: true });
    if (kind === 'run') await removeRun(item);
    else await removeAssessment(item);
    setPendingDelete(null);
  }

  const pendingLabel = pendingDelete
    ? pendingDelete.kind === 'run'
      ? pendingDelete.item.file
      : pendingDelete.item.name
    : null;

  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-surface">
      <div className="px-5 pt-6 pb-5">
        <img src={logoUrl} alt="dataskate" className="h-[18px] w-auto" />
        <p className="mt-2 text-[11px] font-semibold tracking-[0.08em] text-ink-3 uppercase">
          DQ Accelerator
        </p>
      </div>

      <div className="px-3">
        <NavLink
          to="/dashboard"
          className={({ isActive }) =>
            `mb-1 flex items-center gap-2 rounded-[var(--radius-control)] px-3 py-2 text-sm font-semibold transition ${
              isActive ? 'bg-accent text-white' : 'text-ink-2 hover:bg-ink/4'
            }`
          }
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 16 16"
            className="size-3.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
          >
            <path d="M3 13V8M8 13V3M13 13v-3" />
          </svg>
          Dashboard
        </NavLink>
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
        <NavLink
          to="/salesforce"
          className={({ isActive }) =>
            `mt-1 flex items-center gap-2 rounded-[var(--radius-control)] px-3 py-2 text-sm font-semibold transition ${
              isActive ? 'bg-accent text-white' : 'text-accent hover:bg-accent/8'
            }`
          }
        >
          <span aria-hidden="true" className="text-base leading-none">
            +
          </span>
          Connect Salesforce
        </NavLink>
      </div>

      <div className="mt-5 flex items-center justify-between px-5 pb-1">
        {selectMode ? (
          <>
            <button
              type="button"
              onClick={() => setChecked(allChecked ? new Set() : new Set(selectableKeys))}
              className="text-xs font-semibold text-accent transition hover:text-accent-hover"
            >
              {allChecked ? 'Clear all' : 'Select all'}
            </button>
            <button
              type="button"
              onClick={exitSelectMode}
              className="text-xs font-semibold text-ink-2 transition hover:text-ink"
            >
              Cancel
            </button>
          </>
        ) : (
          (runs?.length > 0 || assessments?.length > 0) && (
            <button
              type="button"
              onClick={() => setSelectMode(true)}
              className="ml-auto text-xs font-semibold text-ink-3 transition hover:text-ink"
            >
              Select
            </button>
          )
        )}
      </div>

      <nav aria-label="Assessment runs" className="min-h-0 flex-1 overflow-y-auto px-3 pb-6">
        <SidebarSection
          id="sidebar-runs"
          title="Runs"
          count={runs?.length ?? 0}
          reviewCount={countReview(runs)}
        >
          {isLoadingRuns && <p className="px-3 py-2 text-xs text-ink-3">Loading runs&hellip;</p>}

          {runsError && <p className="px-3 py-2 text-xs text-critical">Could not load runs.</p>}

          {runs?.length === 0 && <p className="px-3 py-2 text-xs text-ink-3">No runs yet.</p>}

          <ul className="space-y-1">
            {runs?.map((run) => (
              <RunListItem
                key={run.id}
                run={run}
                label={run.file}
                isSelected={run.id === selectedRunId}
                onSelect={selectRun}
                onRequestDelete={(item) => setPendingDelete({ kind: 'run', item })}
                selectMode={selectMode}
                isChecked={checked.has(`run:${run.id}`)}
                onToggleCheck={() => toggleCheck(`run:${run.id}`)}
                checkDisabled={checkDisabledFor(run)}
                badges={
                  (run.assessmentId || run.source?.type === 'salesforce') && (
                    <Badge tone="salesforce" title="An object from a Salesforce assessment">
                      Salesforce
                    </Badge>
                  )
                }
              />
            ))}
          </ul>
        </SidebarSection>

        <SidebarSection
          id="sidebar-salesforce"
          title="Salesforce"
          count={assessments?.length ?? 0}
          reviewCount={countReview(assessments)}
        >
          {!assessments?.length && (
            <p className="px-3 py-2 text-xs text-ink-3">
              No Salesforce assessments yet.{' '}
              <NavLink
                to="/salesforce"
                className="font-semibold text-accent hover:text-accent-hover"
              >
                Connect an org
              </NavLink>
            </p>
          )}
          <ul className="space-y-1">
            {assessments?.map((assessment) => {
              const isOpen = expanded.has(assessment.id);
              return (
                <RunListItem
                  key={assessment.id}
                  run={assessment}
                  label={assessment.name}
                  isSelected={assessment.id === selectedAssessmentId}
                  onSelect={selectAssessment}
                  onRequestDelete={(item) => setPendingDelete({ kind: 'assessment', item })}
                  selectMode={selectMode}
                  isChecked={checked.has(`assessment:${assessment.id}`)}
                  onToggleCheck={() => toggleCheck(`assessment:${assessment.id}`)}
                  badges={
                    assessment.name.endsWith('(Sandbox)') && <Badge tone="sandbox">Sandbox</Badge>
                  }
                  expander={
                    <button
                      type="button"
                      onClick={() => toggleExpanded(assessment.id)}
                      aria-expanded={isOpen}
                      aria-label={`${isOpen ? 'Hide' : 'Show'} objects in ${assessment.name}`}
                      className="mt-2 ml-1 shrink-0 rounded p-1 text-ink-3 transition hover:bg-ink/6 hover:text-ink"
                    >
                      <Chevron open={isOpen} />
                    </button>
                  }
                >
                  {isOpen && !selectMode && (
                    <AssessmentObjects
                      assessment={assessment}
                      selectedRunId={selectedRunId}
                      onSelectRun={selectRun}
                    />
                  )}
                </RunListItem>
              );
            })}
          </ul>
        </SidebarSection>
      </nav>

      {selectMode && (
        <div className="flex items-center justify-between gap-2 border-t border-border px-4 py-3">
          <span className="text-xs text-ink-2">{checkedCount} selected</span>
          <button
            type="button"
            onClick={() => setIsBulkConfirmOpen(true)}
            disabled={checkedCount === 0}
            className="rounded-[var(--radius-control)] bg-critical px-3 py-1.5 text-xs font-semibold text-white transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Delete {checkedCount || ''}
          </button>
        </div>
      )}

      {isBulkConfirmOpen && (
        <ConfirmDialog
          title={`Delete ${checkedCount} ${checkedCount === 1 ? 'item' : 'items'}?`}
          body={
            <>
              <ul className="mb-2 space-y-0.5">
                {checkedNames.slice(0, 5).map((name) => (
                  <li key={name} className="truncate font-mono text-[13px] text-ink">
                    {name}
                  </li>
                ))}
                {checkedNames.length > 5 && (
                  <li className="text-xs text-ink-3">and {checkedNames.length - 5} more</li>
                )}
              </ul>
              Their reports
              {checkedAssessments.length > 0 && ', Salesforce objects and downloaded data'} will be
              removed.
              {checkedInProgress > 0 &&
                ` ${checkedInProgress} still being assessed will be cancelled.`}{' '}
              This can&rsquo;t be undone.
            </>
          }
          confirmLabel={`Delete ${checkedCount}`}
          isPending={isBulkDeleting}
          onConfirm={confirmBulkDelete}
          onCancel={() => setIsBulkConfirmOpen(false)}
        />
      )}

      {pendingDelete && (
        <ConfirmDialog
          title={pendingDelete.kind === 'run' ? 'Delete this run?' : 'Delete this assessment?'}
          body={
            <>
              <span className="font-mono text-[13px] text-ink">{pendingLabel}</span>
              {[STATUS.PROCESSING, STATUS.AWAITING_CDES].includes(pendingDelete.item.status)
                ? ' is still being assessed. Deleting it cancels the assessment.'
                : pendingDelete.kind === 'run'
                  ? ' and its report will be removed.'
                  : ', every object in it, and all downloaded Salesforce data will be removed.'}{' '}
              This can&rsquo;t be undone.
            </>
          }
          isPending={deletingId === pendingDelete.item.id}
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </aside>
  );
}
