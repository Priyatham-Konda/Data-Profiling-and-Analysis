import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { Toast, ToastProvider } from '@/components/Toast';
import { RunsProvider } from '@/components/RunsProvider';
import * as runsApi from '@/api/runs';
import * as sfApi from '@/api/salesforce';
import { DashboardPanel } from './DashboardPanel';

vi.mock('@/api/runs', async (importOriginal) => ({
  ...(await importOriginal()),
  listRuns: vi.fn(),
  getRun: vi.fn(),
}));

vi.mock('@/api/salesforce', async (importOriginal) => ({
  ...(await importOriginal()),
  listAssessments: vi.fn(),
  getAssessment: vi.fn(),
}));

const SEVEN = (base, overrides = {}) => ({
  completeness: base,
  validity: base,
  uniqueness: base,
  consistency: base,
  accuracy: base,
  timeliness: base,
  integrity: base,
  ...overrides,
});

const RUNS = [
  { id: 'run_a', file: 'customers.csv', status: 'completed', overall: 92 },
  { id: 'run_b', file: 'orders.csv', status: 'completed', overall: 60 },
  { id: 'run_c', file: 'vendors.csv', status: 'awaiting_cdes' },
  // An object run: part of its assessment's result, not a file of its own.
  { id: 'run_o', file: 'Acme · Account', status: 'completed', overall: 10, assessmentId: 'asm_1' },
];

const DETAILS = {
  run_a: { scores: SEVEN(90, { timeliness: null }) },
  run_b: { scores: SEVEN(60, { timeliness: null }) },
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function renderDashboard() {
  return render(
    <ToastProvider>
      <RunsProvider>
        <MemoryRouter initialEntries={['/dashboard']}>
          <Routes>
            <Route path="/dashboard" element={<DashboardPanel />} />
            <Route path="/" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
        <Toast />
      </RunsProvider>
    </ToastProvider>,
  );
}

describe('DashboardPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    runsApi.listRuns.mockResolvedValue(RUNS);
    runsApi.getRun.mockImplementation((id) => Promise.resolve(DETAILS[id]));
    sfApi.listAssessments.mockResolvedValue([
      { id: 'asm_1', name: 'Acme Corporation', status: 'completed', overall: 80, objects: 2 },
    ]);
    sfApi.getAssessment.mockResolvedValue({ scores: SEVEN(80) });
  });

  it('draws a bar out of 100 per scored result, leaving out object runs', async () => {
    renderDashboard();

    expect(await screen.findByRole('button', { name: 'orders.csv: 60.0 out of 100' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'customers.csv: 92.0 out of 100' })).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Acme Corporation: 80.0 out of 100' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Acme · Account/ })).not.toBeInTheDocument();
  });

  it('averages each dimension across results, skipping not-assessed ones', async () => {
    renderDashboard();

    // Completeness: (90 + 60 + 80) / 3. Timeliness: only Acme assessed it.
    expect(
      await screen.findByRole('img', { name: 'Completeness: 76.7 out of 100, from 3 scored' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('img', { name: 'Timeliness: 80.0 out of 100, from 1 scored' }),
    ).toBeInTheDocument();
  });

  it('filters to files and opens a result when its bar is clicked', async () => {
    const user = userEvent.setup();
    renderDashboard();

    await screen.findByRole('button', { name: /Acme Corporation:/ });
    await user.click(
      within(screen.getByRole('group', { name: 'Show' })).getByRole('button', { name: 'Files' }),
    );
    expect(screen.queryByRole('button', { name: /Acme Corporation:/ })).not.toBeInTheDocument();
    expect(screen.getByText('Waiting on review').nextSibling).toHaveTextContent('1');

    await user.click(screen.getByRole('button', { name: 'orders.csv: 60.0 out of 100' }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/?run=run_b'),
    );
  });
});
