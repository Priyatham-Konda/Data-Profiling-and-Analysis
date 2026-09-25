import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AwaitingCdesView } from './AwaitingCdesView';
import { Toast, ToastProvider } from '@/components/Toast';
import { RunsProvider } from '@/components/RunsProvider';
import * as api from '@/api/runs';

vi.mock('@/api/runs', async (importOriginal) => ({
  ...(await importOriginal()),
  getRunProfile: vi.fn(),
  overrideCdes: vi.fn(),
  listRuns: vi.fn().mockResolvedValue([]),
}));

const RUN = { id: 'run_1', file: 'customers.csv', records: 1000, columns: 34, cdes: 18 };

const PROFILE = {
  columns: [
    { name: 'customer_id', inferredType: 'string', fillRate: 0.98, distinctRatio: 0.99, isCde: true, cdeReason: 'Looks like an identifier' },
    { name: 'created_by', inferredType: 'string', fillRate: 1, distinctRatio: 0.0001, isCde: false, cdeReason: 'Single repeated value' },
  ],
};

function renderView(props = {}) {
  return render(
    <ToastProvider>
      <RunsProvider>
        <AwaitingCdesView run={RUN} onConfirmed={vi.fn()} {...props} />
        <Toast />
      </RunsProvider>
    </ToastProvider>,
  );
}

describe('AwaitingCdesView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listRuns.mockResolvedValue([]);
  });

  it('shows the auto-detected counts immediately, then the checklist once the profile loads', async () => {
    api.getRunProfile.mockResolvedValue(PROFILE);
    renderView();

    // Available right away from the run object, no fetch needed.
    expect(screen.getByText(/34 columns/)).toBeInTheDocument();
    expect(screen.getByText(/18 as critical data elements/)).toBeInTheDocument();

    expect(await screen.findByText('customer_id')).toBeInTheDocument();
    expect(screen.getByText('created_by')).toBeInTheDocument();
    expect(api.getRunProfile).toHaveBeenCalledWith('run_1');
  });

  it('seeds the checklist from the detected defaults', async () => {
    api.getRunProfile.mockResolvedValue(PROFILE);
    renderView();

    await screen.findByText('customer_id');
    expect(screen.getByLabelText(/use customer_id as a critical data element/i)).toBeChecked();
    expect(
      screen.getByLabelText(/use created_by as a critical data element/i),
    ).not.toBeChecked();
    expect(screen.getByText(/1 of 2 columns selected/i)).toBeInTheDocument();
  });

  it('disables confirm once every column is unchecked', async () => {
    const user = userEvent.setup();
    api.getRunProfile.mockResolvedValue(PROFILE);
    renderView();

    await screen.findByText('customer_id');
    await user.click(screen.getByLabelText(/use customer_id as a critical data element/i));

    expect(screen.getByRole('button', { name: /confirm and start scoring/i })).toBeDisabled();
  });

  it('confirming calls overrideCdes with the current selection, toasts, refreshes runs, and calls onConfirmed', async () => {
    const user = userEvent.setup();
    api.getRunProfile.mockResolvedValue(PROFILE);
    api.overrideCdes.mockResolvedValue({ id: 'run_1', status: 'processing' });
    const onConfirmed = vi.fn();
    renderView({ onConfirmed });

    await screen.findByText('customer_id');
    await user.click(screen.getByRole('button', { name: /confirm and start scoring/i }));

    await waitFor(() => expect(api.overrideCdes).toHaveBeenCalledWith('run_1', ['customer_id']));
    expect(onConfirmed).toHaveBeenCalled();
    expect(await screen.findByRole('status')).toHaveTextContent(/confirmed/i);
  });

  it('reports a confirm failure through the toast without calling onConfirmed', async () => {
    const user = userEvent.setup();
    api.getRunProfile.mockResolvedValue(PROFILE);
    api.overrideCdes.mockRejectedValue(new Error('Select at least one column.'));
    const onConfirmed = vi.fn();
    renderView({ onConfirmed });

    await screen.findByText('customer_id');
    await user.click(screen.getByRole('button', { name: /confirm and start scoring/i }));

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/could not start scoring/i),
    );
    expect(onConfirmed).not.toHaveBeenCalled();
  });
});
