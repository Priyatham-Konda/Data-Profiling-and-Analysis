import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ColumnProfilePanel } from './ColumnProfilePanel';
import { Toast, ToastProvider } from '@/components/Toast';
import { RunsProvider } from '@/components/RunsProvider';
import * as api from '@/api/runs';

vi.mock('@/api/runs', async (importOriginal) => ({
  ...(await importOriginal()),
  getRunProfile: vi.fn(),
  overrideCdes: vi.fn(),
  listRuns: vi.fn().mockResolvedValue([]),
}));

const PROFILE = {
  columns: [
    {
      name: 'customer_id', inferredType: 'string', semanticType: 'identifier',
      fillRate: 0.98, distinctRatio: 0.99, sampleValues: ['ID-1', 'ID-2', 'ID-3'],
      isCde: true, cdeScore: 0.9, cdeReason: 'Looks like an identifier',
    },
    {
      name: 'created_by', inferredType: 'string', semanticType: 'metadata',
      fillRate: 1, distinctRatio: 0.0001, sampleValues: ['ETL_LOAD', 'ETL_LOAD', 'ETL_LOAD'],
      isCde: false, cdeScore: 0.05, cdeReason: 'Single repeated value',
    },
  ],
};

// ColumnProfilePanel reads RunsContext (refreshRuns) and ToastContext
// (showToast) directly, so it needs both providers -- and Toast itself
// mounted, or a fired toast updates state nothing renders.
function renderPanel(props = {}) {
  return render(
    <ToastProvider>
      <RunsProvider>
        <ColumnProfilePanel runId="run_1" onClose={vi.fn()} {...props} />
        <Toast />
      </RunsProvider>
    </ToastProvider>,
  );
}

describe('ColumnProfilePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listRuns.mockResolvedValue([]);
  });

  it('fetches lazily and renders the column table with a CDE summary', async () => {
    api.getRunProfile.mockResolvedValue(PROFILE);
    renderPanel();

    expect(screen.getByText(/loading column profile/i)).toBeInTheDocument();
    expect(await screen.findByText('customer_id')).toBeInTheDocument();
    expect(screen.getByText('created_by')).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 columns selected/i)).toBeInTheDocument();
    expect(api.getRunProfile).toHaveBeenCalledWith('run_1');
  });

  it('closes on Escape without needing a document-level listener', async () => {
    const user = userEvent.setup();
    api.getRunProfile.mockResolvedValue(PROFILE);
    const onClose = vi.fn();
    renderPanel({ onClose });

    await screen.findByText('customer_id');
    await user.keyboard('{Escape}');

    expect(onClose).toHaveBeenCalled();
  });

  it('surfaces a fetch failure', async () => {
    api.getRunProfile.mockRejectedValue(new Error('Profile not available.'));
    renderPanel();

    await waitFor(() =>
      expect(screen.getByText(/profile not available/i)).toBeInTheDocument(),
    );
  });

  it('seeds checkboxes from the detected CDEs and updates the count on toggle', async () => {
    const user = userEvent.setup();
    api.getRunProfile.mockResolvedValue(PROFILE);
    renderPanel();

    await screen.findByText('customer_id');
    expect(screen.getByLabelText(/use customer_id as a critical data element/i)).toBeChecked();
    expect(
      screen.getByLabelText(/use created_by as a critical data element/i),
    ).not.toBeChecked();

    await user.click(screen.getByLabelText(/use created_by as a critical data element/i));

    expect(screen.getByText(/2 of 2 columns selected/i)).toBeInTheDocument();
    expect(screen.getAllByText('(changed)')).toHaveLength(1);
  });

  it('disables Re-assess once every column is unchecked', async () => {
    const user = userEvent.setup();
    api.getRunProfile.mockResolvedValue(PROFILE);
    renderPanel();

    await screen.findByText('customer_id');
    await user.click(screen.getByLabelText(/use customer_id as a critical data element/i));

    expect(screen.getByRole('button', { name: /re-assess/i })).toBeDisabled();
  });

  it('re-assesses with the current selection, toasts, refreshes runs, and closes', async () => {
    const user = userEvent.setup();
    api.getRunProfile.mockResolvedValue(PROFILE);
    api.overrideCdes.mockResolvedValue({ id: 'run_1', status: 'processing' });
    const onClose = vi.fn();
    const onReassessed = vi.fn();
    renderPanel({ onClose, onReassessed });

    await screen.findByText('customer_id');
    await user.click(screen.getByLabelText(/use created_by as a critical data element/i));
    await user.click(screen.getByRole('button', { name: /re-assess/i }));

    await waitFor(() =>
      expect(api.overrideCdes).toHaveBeenCalledWith(
        'run_1',
        expect.arrayContaining(['customer_id', 'created_by']),
      ),
    );
    expect(onReassessed).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
    // listRuns is called again by refreshRuns() -- once on RunsProvider mount,
    // once after the override.
    expect(api.listRuns).toHaveBeenCalledTimes(2);
  });

  it('reports a re-assess failure through the toast and leaves the panel open', async () => {
    const user = userEvent.setup();
    api.getRunProfile.mockResolvedValue(PROFILE);
    api.overrideCdes.mockRejectedValue(new Error('Column "foo" was not found in this file.'));
    const onClose = vi.fn();
    renderPanel({ onClose });

    await screen.findByText('customer_id');
    await user.click(screen.getByRole('button', { name: /re-assess/i }));

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/foo.*was not found/i),
    );
    expect(onClose).not.toHaveBeenCalled();
  });
});
