import { useContext, useEffect, useState } from 'react';
import { STATUS } from '@/api/constants';
import { getRunProfile } from '@/api/runs';
import { confirmAssessmentCdes } from '@/api/salesforce';
import { RunsContext } from '@/components/RunsProvider';
import { ToastContext } from '@/components/Toast';
import { ColumnChecklist } from '../home/ColumnChecklist';
import { AssessmentHeading } from '../AssessmentPanel';

/**
 * Revision 5's awaiting_cdes stop for a whole assessment: one tab per object,
 * each with the same checklist AwaitingCdesView uses, and ONE confirm that
 * sends every object's list together (PUT /assessments/{id}/cdes). Object
 * runs can't be confirmed one at a time -- the backend answers 409.
 */
export function AssessmentConfirmView({ assessment, onConfirmed }) {
  const { refreshAssessments } = useContext(RunsContext);
  const { showToast } = useContext(ToastContext);

  const awaiting = assessment.runs.filter((run) => run.status === STATUS.AWAITING_CDES);
  const [activeObject, setActiveObject] = useState(awaiting[0]?.object ?? assessment.runs[0]?.object);

  // object name -> { data, error } for each awaiting object's profile.
  const [profiles, setProfiles] = useState({});
  // object name -> Set of checked column names.
  const [selections, setSelections] = useState({});
  const [isSaving, setIsSaving] = useState(false);

  // Seed a tab's selection from its detected CDEs the moment its profile
  // arrives -- adjusted during render, same as AwaitingCdesView, so no frame
  // shows empty checkboxes.
  const [seeded, setSeeded] = useState(profiles);
  if (seeded !== profiles) {
    setSeeded(profiles);
    const next = { ...selections };
    let changed = false;
    Object.entries(profiles).forEach(([object, { data }]) => {
      if (data && !next[object]) {
        next[object] = new Set(data.columns.filter((c) => c.isCde).map((c) => c.name));
        changed = true;
      }
    });
    if (changed) setSelections(next);
  }

  // The list of awaiting run ids is stable while this view is mounted (the
  // assessment only leaves awaiting_cdes by this view's own confirm), so a
  // joined string makes a safe dependency.
  const awaitingKey = awaiting.map((run) => run.id).join(',');
  useEffect(() => {
    let cancelled = false;
    const targets = assessment.runs.filter((run) => run.status === STATUS.AWAITING_CDES);
    targets.forEach((run) => {
      getRunProfile(run.id).then(
        (data) => {
          if (!cancelled) setProfiles((prev) => ({ ...prev, [run.object]: { data, error: null } }));
        },
        (error) => {
          if (!cancelled) setProfiles((prev) => ({ ...prev, [run.object]: { data: null, error } }));
        },
      );
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on awaitingKey on purpose
  }, [awaitingKey]);

  function toggleColumn(object, name) {
    setSelections((prev) => {
      const next = new Set(prev[object]);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return { ...prev, [object]: next };
    });
  }

  const allReady = awaiting.every((run) => selections[run.object]?.size > 0);

  async function handleConfirm() {
    setIsSaving(true);
    try {
      const objects = Object.fromEntries(
        awaiting.map((run) => [run.object, [...selections[run.object]]]),
      );
      await confirmAssessmentCdes(assessment.id, objects);
      showToast(`${assessment.name} confirmed. Scoring now.`);
      refreshAssessments();
      onConfirmed?.();
    } catch (err) {
      showToast(`Could not start scoring. ${err.message}`, 'error');
    } finally {
      setIsSaving(false);
    }
  }

  const active = assessment.runs.find((run) => run.object === activeObject);
  const profile = profiles[activeObject];
  const failedCount = assessment.runs.length - awaiting.length;

  return (
    <div className="px-10 py-9">
      <AssessmentHeading assessment={assessment}>Review critical data elements</AssessmentHeading>
      <p className="mt-2 max-w-2xl text-sm text-ink-2">
        {assessment.name}: {awaiting.length} of {assessment.runs.length} objects are profiled.
        Check the detected critical data elements for each one, then start the analysis for all
        of them at once.
        {failedCount > 0 && ' Objects that failed are left out of the scores.'}
      </p>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <div role="tablist" aria-label="Objects" className="flex flex-wrap gap-1.5">
          {assessment.runs.map((run) => {
            const isActive = run.object === activeObject;
            const failed = run.status === STATUS.FAILED;
            return (
              <button
                key={run.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => setActiveObject(run.object)}
                className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                  isActive ? 'bg-accent text-white' : 'bg-ink/5 text-ink-2 hover:bg-ink/8'
                } ${failed && !isActive ? 'text-critical' : ''}`}
              >
                {run.label}
                {failed && ' (failed)'}
                {!failed && selections[run.object] && (
                  <span className="ml-1 font-mono opacity-80">{selections[run.object].size}</span>
                )}
              </button>
            );
          })}
        </div>
        <button
          type="button"
          onClick={handleConfirm}
          disabled={!allReady || isSaving}
          className="rounded-[var(--radius-control)] bg-accent px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isSaving ? 'Starting…' : 'Start analysis'}
        </button>
      </div>

      <div role="tabpanel" className="mt-5">
        {active?.status === STATUS.FAILED && (
          <div className="rounded-[var(--radius-card)] border border-critical/25 bg-critical/6 px-5 py-4">
            <p className="text-xs font-semibold tracking-[0.06em] text-critical uppercase">
              Could not assess {active.label}
            </p>
            <p className="mt-1.5 text-sm leading-relaxed text-ink-2">{active.error}</p>
          </div>
        )}

        {active?.status === STATUS.AWAITING_CDES && !profile && (
          <p className="text-sm text-ink-3">Loading detected columns&hellip;</p>
        )}
        {profile?.error && (
          <p className="text-sm text-critical">
            {profile.error.message ?? 'Could not load the column profile.'}
          </p>
        )}
        {profile?.data && selections[activeObject] && (
          <>
            <p className="mb-3 text-xs text-ink-3">
              {selections[activeObject].size} of {profile.data.columns.length} fields selected
            </p>
            <ColumnChecklist
              columns={profile.data.columns}
              selected={selections[activeObject]}
              onToggle={(name) => toggleColumn(activeObject, name)}
            />
          </>
        )}
      </div>
    </div>
  );
}
