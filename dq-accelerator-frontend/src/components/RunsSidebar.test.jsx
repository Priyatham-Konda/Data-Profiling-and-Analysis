import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { Toast, ToastProvider } from './Toast';
import { RunsProvider } from './RunsProvider';
import { AppShell } from './AppShell';
import * as api from '@/api/runs';

// No Salesforce assessments unless a test says otherwise -- without this the
// list would come from a real request that merely happens to fail.
vi.mock('@/api/salesforce', async (importOriginal) => ({
  ...(await importOriginal()),
  listAssessments: vi.fn().mockResolvedValue([]),
}));

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

describe('sidebar sections', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listRuns.mockResolvedValue([
      ...RUNS,
      { id: 'run_d', file: 'Acme · Account', status: 'completed', overall: 80, assessmentId: 'asm_1' },
    ]);
    api.getRun.mockResolvedValue(RUNS[0]);
  });

  it('shows a count and how many runs wait on a CDE review, even when collapsed', async () => {
    const user = userEvent.setup();
    renderSidebar();

    const header = await screen.findByRole('button', { name: /^runs/i });
    await waitFor(() => expect(header).toHaveTextContent('4'));
    expect(header).toHaveTextContent('1 to review');

    await user.click(header);
    expect(header).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTitle('customers.csv')).not.toBeInTheDocument();
    expect(header).toHaveTextContent('1 to review');
  });

  it('badges Salesforce object runs', async () => {
    renderSidebar();
    const row = (await screen.findByTitle('Acme · Account')).closest('li');
    expect(within(row).getByText('Salesforce')).toBeInTheDocument();
    expect(within(rowFor('customers.csv')).queryByText('Salesforce')).not.toBeInTheDocument();
  });
});

describe('deleting several runs at once', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listRuns.mockResolvedValue([
      ...RUNS,
      { id: 'run_d', file: 'Acme · Account', status: 'completed', overall: 80, assessmentId: 'asm_1' },
    ]);
    api.getRun.mockResolvedValue(RUNS[0]);
    api.deleteRun.mockResolvedValue(null);
  });

  it('ticks rows instead of opening them, confirms once, and deletes every ticked run', async () => {
    const user = userEvent.setup();
    renderSidebar();

    await user.click(await screen.findByRole('button', { name: /^select$/i }));
    await user.click(screen.getByTitle('customers.csv'));
    await user.click(screen.getByRole('checkbox', { name: /select orders\.csv/i }));

    // Ticking didn't navigate.
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/$/);
    expect(screen.getByText('2 selected')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^delete 2$/i }));
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText('customers.csv')).toBeInTheDocument();
    expect(within(dialog).getByText(/will be cancelled/)).toBeInTheDocument();
    expect(api.deleteRun).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: /^delete 2$/i }));

    await waitFor(() => expect(api.deleteRun).toHaveBeenCalledTimes(2));
    expect(api.deleteRun).toHaveBeenCalledWith('run_a');
    expect(api.deleteRun).toHaveBeenCalledWith('run_b');
    expect(await screen.findByText('2 items were deleted')).toBeInTheDocument();
    // Select mode ends after a bulk delete.
    await waitFor(() => expect(screen.queryByText(/selected$/)).not.toBeInTheDocument());
  });

  it("won't tick an object run, which is deleted with its assessment", async () => {
    const user = userEvent.setup();
    renderSidebar();

    await user.click(await screen.findByRole('button', { name: /^select$/i }));
    expect(screen.getByRole('checkbox', { name: /select acme · account/i })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /^select all$/i }));
    expect(screen.getByText(/ selected$/).textContent).toBe('3 selected');
  });

  it('cancel leaves everything alone', async () => {
    const user = userEvent.setup();
    renderSidebar();

    await user.click(await screen.findByRole('button', { name: /^select$/i }));
    await user.click(screen.getByTitle('customers.csv'));
    await user.click(screen.getByRole('button', { name: /^cancel$/i }));

    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(api.deleteRun).not.toHaveBeenCalled();
  });
});
