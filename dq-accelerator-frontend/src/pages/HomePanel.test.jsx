import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { MainState } from './HomePanel';
import { STATUS } from '@/api/constants';
import { ToastProvider } from '@/components/Toast';
import { RunsProvider } from '@/components/RunsProvider';

vi.mock('@/api/runs', async (importOriginal) => ({
  ...(await importOriginal()),
  getRunProfile: vi.fn(() => new Promise(() => {})), // never resolves -- these tests don't need it to
  listRuns: vi.fn().mockResolvedValue([]),
}));

// MainState is a pure function of (selectedRunId, run.status), so it can be
// exercised without a query client, a router param, or any network. It's
// wrapped in ToastProvider/RunsProvider because a completed run's
// DownloadMenu, and the awaiting_cdes view, both read those contexts.
function renderState(props) {
  return render(
    <ToastProvider>
      <RunsProvider>
        <MemoryRouter>
          <MainState isPending={false} isError={false} onOpenDimension={() => {}} {...props} />
        </MemoryRouter>
      </RunsProvider>
    </ToastProvider>,
  );
}

describe('Home main panel', () => {
  it('shows the empty state when no run is selected', () => {
    renderState({ selectedRunId: null });
    expect(screen.getByText(/select a run to see its report/i)).toBeInTheDocument();
  });

  it('shows stage, progress and safe-to-leave copy while processing', () => {
    renderState({
      selectedRunId: 'run_1',
      run: {
        id: 'run_1',
        file: 'orders.csv',
        status: STATUS.PROCESSING,
        stage: 'Profiling',
        stageIndex: 1,
        stageCount: 4,
        progress: 0.5,
      },
    });
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
    expect(screen.getByText(/Profiling/)).toBeInTheDocument();
    expect(screen.getByText(/safe to leave this page/i)).toBeInTheDocument();
  });

  it('shows the error and a way back to upload when failed', () => {
    renderState({
      selectedRunId: 'run_2',
      run: {
        id: 'run_2',
        file: 'vendor.csv',
        status: STATUS.FAILED,
        error: 'Could not parse row 4,812.',
      },
    });
    expect(screen.getByText(/could not parse row 4,812/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /upload a corrected file/i })).toHaveAttribute(
      'href',
      '/upload',
    );
  });

  it('shows the review-CDEs view when awaiting_cdes, not a processing screen', () => {
    renderState({
      selectedRunId: 'run_4',
      run: {
        id: 'run_4',
        file: 'orders.csv',
        status: STATUS.AWAITING_CDES,
        records: 1000,
        columns: 34,
        cdes: 18,
      },
    });

    expect(screen.getByText(/review critical data elements/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /confirm and start scoring/i })).toBeInTheDocument();
    // Not the auto-advancing processing screen -- this state requires input.
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('shows the score header, downloads and six tiles when completed', () => {
    renderState({
      selectedRunId: 'run_3',
      run: {
        id: 'run_3',
        file: 'customers.csv',
        status: STATUS.COMPLETED,
        overall: 82.4,
        records: 1000,
        cdes: 12,
        scores: {
          completeness: 96,
          validity: 91,
          uniqueness: 84,
          consistency: 77,
          accuracy: 68,
          timeliness: 61,
          integrity: 82,
        },
      },
    });

    expect(screen.getByText('82.4')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /download summary/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /in-depth report/i })).toBeInTheDocument();
    // Seven dimension tiles, each a button (keyboard-reachable).
    expect(screen.getAllByTitle(/double-click for rule detail/i)).toHaveLength(7);
    // Bands are derived, not stored.
    expect(screen.getAllByText('Healthy')).toHaveLength(2);
    expect(screen.getAllByText('Critical')).toHaveLength(2);
  });
});
