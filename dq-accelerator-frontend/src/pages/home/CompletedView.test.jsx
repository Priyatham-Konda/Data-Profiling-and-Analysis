import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CompletedView } from './CompletedView';
import { Toast, ToastProvider } from '@/components/Toast';
import { RunsProvider } from '@/components/RunsProvider';
import * as api from '@/api/runs';

vi.mock('@/api/runs', async (importOriginal) => ({
  ...(await importOriginal()),
  downloadReport: vi.fn(),
  getRunProfile: vi.fn(),
  listRuns: vi.fn().mockResolvedValue([]),
}));

const RUN = {
  id: 'run_1',
  file: 'customers.csv',
  overall: 91,
  records: 1000,
  cdes: 10,
  scores: {
    completeness: 96,
    validity: 91,
    uniqueness: 84,
    consistency: 77,
    accuracy: 68,
    timeliness: 61,
    integrity: 82,
  },
};

function renderView() {
  return render(
    <ToastProvider>
      <RunsProvider>
        <CompletedView run={RUN} onOpenDimension={() => {}} />
        <Toast />
      </RunsProvider>
    </ToastProvider>,
  );
}

describe('DownloadMenu', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // Regression test: MSW (a Service Worker) doesn't reliably see requests made
  // by clicking an <a download>, so left alone those clicks fall through to
  // Vite's dev server and download its index.html shell instead of the mocked
  // report. In dev the click must be intercepted and re-issued as a real
  // fetch() via downloadReport instead of following the href.
  it('intercepts the click in dev and calls downloadReport instead of navigating', async () => {
    const user = userEvent.setup();
    api.downloadReport.mockResolvedValue(undefined);
    renderView();

    await user.click(screen.getByRole('link', { name: /download summary/i }));

    expect(api.downloadReport).toHaveBeenCalledWith('run_1', 'summary');
  });

  it('reports a download failure through the toast', async () => {
    const user = userEvent.setup();
    api.downloadReport.mockRejectedValue(new Error('Report not ready.'));
    renderView();

    await user.click(screen.getByRole('link', { name: /in-depth report/i }));

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/could not download the report/i),
    );
  });
});

describe('column profile trigger', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('opens the column profile panel from the run summary', async () => {
    const user = userEvent.setup();
    api.getRunProfile.mockResolvedValue({ columns: [] });
    renderView();

    expect(screen.queryByRole('dialog', { name: /column profile/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /view column profile/i }));

    expect(await screen.findByRole('dialog', { name: /column profile/i })).toBeInTheDocument();
    expect(api.getRunProfile).toHaveBeenCalledWith('run_1');
  });
});
