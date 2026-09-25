import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { Toast, ToastProvider } from './Toast';
import { RunsProvider } from './RunsProvider';
import { AppShell } from './AppShell';
import * as api from '@/api/runs';

vi.mock('@/api/runs', async (importOriginal) => ({
  ...(await importOriginal()),
  listRuns: vi.fn(),
  getRun: vi.fn(),
  deleteRun: vi.fn(),
}));

const RUNS = [
  { id: 'run_a', file: 'customers.csv', status: 'completed', overall: 91 },
  { id: 'run_b', file: 'orders.csv', status: 'processing' },
  { id: 'run_c', file: 'vendors.csv', status: 'awaiting_cdes' },
];

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function renderSidebar(initialEntry = '/') {
  return render(
    <ToastProvider>
      <RunsProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<LocationProbe />} />
            </Route>
          </Routes>
        </MemoryRouter>
        <Toast />
      </RunsProvider>
    </ToastProvider>,
  );
}

function rowFor(file) {
  return screen.getByTitle(file).closest('li');
}

describe('deleting a run', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listRuns.mockResolvedValue(RUNS);
    api.getRun.mockResolvedValue(RUNS[0]);
    api.deleteRun.mockResolvedValue(null);
  });

  it('asks for confirmation instead of deleting immediately', async () => {
    const user = userEvent.setup();
    renderSidebar();

    await user.click(await screen.findByRole('button', { name: /delete customers\.csv/i }));

    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText(/can.t be undone/i)).toBeInTheDocument();
    expect(api.deleteRun).not.toHaveBeenCalled();
  });

  it('cancelling leaves the run alone', async () => {
    const user = userEvent.setup();
    renderSidebar();

    await user.click(await screen.findByRole('button', { name: /delete customers\.csv/i }));
    await user.click(screen.getByRole('button', { name: /^cancel$/i }));

    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
    expect(api.deleteRun).not.toHaveBeenCalled();
    expect(rowFor('customers.csv')).toBeInTheDocument();
  });

  it('confirming deletes the run and toasts', async () => {
    const user = userEvent.setup();
    renderSidebar();

    await user.click(await screen.findByRole('button', { name: /delete customers\.csv/i }));
    await user.click(screen.getByRole('button', { name: /^delete$/i }));

    await waitFor(() => expect(api.deleteRun).toHaveBeenCalledWith('run_a'));
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/customers\.csv was deleted/i),
    );
  });

  it('warns that deleting a processing run cancels it', async () => {
    const user = userEvent.setup();
    renderSidebar();

    await user.click(await screen.findByRole('button', { name: /delete orders\.csv/i }));
    expect(
      within(await screen.findByRole('alertdialog')).getByText(/cancels the assessment/i),
    ).toBeInTheDocument();
  });

  it('warns the same way for a run awaiting CDE confirmation, not the "report will be removed" copy', async () => {
    const user = userEvent.setup();
    renderSidebar();

    await user.click(await screen.findByRole('button', { name: /delete vendors\.csv/i }));
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText(/cancels the assessment/i)).toBeInTheDocument();
    expect(within(dialog).queryByText(/report will be removed/i)).not.toBeInTheDocument();
  });

  it('clears the selection when the deleted run is the one being viewed', async () => {
    const user = userEvent.setup();
    renderSidebar('/?run=run_a');

    expect(await screen.findByTestId('location')).toHaveTextContent('/?run=run_a');

    await user.click(await screen.findByRole('button', { name: /delete customers\.csv/i }));
    await user.click(screen.getByRole('button', { name: /^delete$/i }));

    // Back to bare Home, so nothing keeps polling an id that no longer exists.
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/'));
    expect(screen.getByTestId('location')).not.toHaveTextContent('run=run_a');
  });

  it('leaves the selection alone when deleting a different run', async () => {
    const user = userEvent.setup();
    renderSidebar('/?run=run_a');

    await user.click(await screen.findByRole('button', { name: /delete orders\.csv/i }));
    await user.click(screen.getByRole('button', { name: /^delete$/i }));

    await waitFor(() => expect(api.deleteRun).toHaveBeenCalledWith('run_b'));
    expect(screen.getByTestId('location')).toHaveTextContent('/?run=run_a');
  });
});

describe('run status line', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listRuns.mockResolvedValue(RUNS);
    api.getRun.mockResolvedValue(RUNS[0]);
  });

  it('shows "Review CDEs" for a run awaiting confirmation, not a score or "Processing"', async () => {
    renderSidebar();
    await screen.findByText('vendors.csv');
    const row = rowFor('vendors.csv');

    expect(within(row).getByText(/review cdes/i)).toBeInTheDocument();
    expect(within(row).queryByText(/processing/i)).not.toBeInTheDocument();
    expect(within(row).queryByText(/score/i)).not.toBeInTheDocument();
  });
});
