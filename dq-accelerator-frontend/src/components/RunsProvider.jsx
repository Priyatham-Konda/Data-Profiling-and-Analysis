import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { deleteRun, listRuns, uploadRun } from '@/api/runs';
import { deleteAssessment, listAssessments } from '@/api/salesforce';
import { STATUS } from '@/api/constants';
import { ToastContext } from './Toast';

export const RunsContext = createContext(null);

/**
 * Owns everything more than one screen needs: the runs list, the upload, and
 * the delete. Mounted at the app root, which matters for upload specifically --
 * "Start assessment" navigates away immediately and unmounts UploadPanel, so a
 * request started inside that component would lose its .then() handler. Started
 * up here, it survives the navigation and can still fire the toast.
 */
export function RunsProvider({ children }) {
  const { showToast } = useContext(ToastContext);

  const [runs, setRuns] = useState(null);
  const [runsError, setRunsError] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [deletingId, setDeletingId] = useState(null);

  // Revision 5: Salesforce assessments are a second list beside runs, polled
  // the same way. A failure here doesn't touch the runs list -- a backend
  // without revision 5 simply shows no Salesforce section.
  const [assessments, setAssessments] = useState(null);

  const refreshRuns = useCallback(async () => {
    try {
      setRuns(await listRuns());
      setRunsError(null);
    } catch (error) {
      setRunsError(error);
    }
  }, []);

  const refreshAssessments = useCallback(async () => {
    try {
      setAssessments(await listAssessments());
    } catch {
      setAssessments((prev) => prev ?? []);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    listAssessments().then(
      (data) => {
        if (!cancelled) setAssessments(data);
      },
      () => {
        if (!cancelled) setAssessments([]);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const assessmentsProcessing = Boolean(assessments?.some((a) => a.status === STATUS.PROCESSING));

  useEffect(() => {
    if (!assessmentsProcessing) return undefined;
    const timer = setInterval(refreshAssessments, 5000);
    return () => clearInterval(timer);
  }, [assessmentsProcessing, refreshAssessments]);

  // Initial load. The state updates sit inside the promise callbacks rather than
  // the effect body, and the cancelled flag stops a late response landing after
  // this provider unmounts.
  useEffect(() => {
    let cancelled = false;

    listRuns().then(
      (data) => {
        if (cancelled) return;
        setRuns(data);
        setRunsError(null);
      },
      (error) => {
        if (!cancelled) setRunsError(error);
      },
    );

    return () => {
      cancelled = true;
    };
  }, []);

  // Poll only while something is actually processing, so an idle app is silent.
  // The dependency is the boolean, not the array: it flips at most twice, so the
  // interval is created once rather than torn down and rebuilt on every refresh.
  const hasProcessing = Boolean(runs?.some((run) => run.status === STATUS.PROCESSING));

  useEffect(() => {
    if (!hasProcessing) return undefined;
    const timer = setInterval(refreshRuns, 5000);
    // Without this cleanup the interval would outlive the component and keep
    // firing requests forever.
    return () => clearInterval(timer);
  }, [hasProcessing, refreshRuns]);

  const startUpload = useCallback(
    async (file) => {
      setIsUploading(true);
      try {
        await uploadRun(file);
        showToast(`${file.name} has been received and is being processed`);
        await refreshRuns();
      } catch (error) {
        // The user is already on Home by now, so the toast is the only channel.
        showToast(`${file.name} could not be uploaded. ${error.message}`, 'error');
      } finally {
        setIsUploading(false);
      }
    },
    [refreshRuns, showToast],
  );

  const removeRun = useCallback(
    async (run) => {
      setDeletingId(run.id);
      try {
        await deleteRun(run.id);
        showToast(`${run.file} was deleted`);
        await refreshRuns();
      } catch (error) {
        showToast(`${run.file} could not be deleted. ${error.message}`, 'error');
      } finally {
        setDeletingId(null);
      }
    },
    [refreshRuns, showToast],
  );

  const removeAssessment = useCallback(
    async (assessment) => {
      setDeletingId(assessment.id);
      try {
        await deleteAssessment(assessment.id);
        showToast(`${assessment.name} was deleted`);
        await refreshAssessments();
      } catch (error) {
        showToast(`${assessment.name} could not be deleted. ${error.message}`, 'error');
      } finally {
        setDeletingId(null);
      }
    },
    [refreshAssessments, showToast],
  );

  // Bulk delete from the sidebar's select mode. Every delete is sent at once
  // and allSettled waits for all of them, so one failure (say, a run the
  // backend refuses) doesn't stop the rest. One toast sums it up.
  const [isBulkDeleting, setIsBulkDeleting] = useState(false);
  const removeMany = useCallback(
    async ({ runs: runItems = [], assessments: assessmentItems = [] }) => {
      setIsBulkDeleting(true);
      const jobs = [
        ...runItems.map((run) => ({ label: run.file, request: () => deleteRun(run.id) })),
        ...assessmentItems.map((a) => ({ label: a.name, request: () => deleteAssessment(a.id) })),
      ];
      const results = await Promise.allSettled(jobs.map((job) => job.request()));
      const failed = results
        .map((result, i) => ({ result, job: jobs[i] }))
        .filter(({ result }) => result.status === 'rejected');
      const deleted = jobs.length - failed.length;

      if (failed.length === 0) {
        showToast(`${deleted} ${deleted === 1 ? 'item was' : 'items were'} deleted`);
      } else {
        const first = failed[0];
        const more = failed.length > 1 ? ` and ${failed.length - 1} more` : '';
        showToast(
          `${deleted} deleted. ${first.job.label}${more} could not be deleted. ${first.result.reason?.message ?? ''}`.trim(),
          'error',
        );
      }

      await Promise.all([refreshRuns(), refreshAssessments()]);
      setIsBulkDeleting(false);
      return { deleted, failed: failed.length };
    },
    [refreshRuns, refreshAssessments, showToast],
  );

  const value = useMemo(
    () => ({
      runs,
      runsError,
      isLoadingRuns: runs === null && !runsError,
      refreshRuns,
      startUpload,
      isUploading,
      removeRun,
      deletingId,
      assessments,
      refreshAssessments,
      removeAssessment,
      removeMany,
      isBulkDeleting,
    }),
    [
      runs,
      runsError,
      refreshRuns,
      startUpload,
      isUploading,
      removeRun,
      deletingId,
      assessments,
      refreshAssessments,
      removeAssessment,
      removeMany,
      isBulkDeleting,
    ],
  );

  return <RunsContext.Provider value={value}>{children}</RunsContext.Provider>;
}
