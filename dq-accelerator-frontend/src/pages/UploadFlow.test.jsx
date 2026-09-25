import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Toast, ToastProvider } from '@/components/Toast';
import { RunsProvider } from '@/components/RunsProvider';
import { AppShell } from '@/components/AppShell';
import { HomePanel } from './HomePanel';
import { UploadPanel } from './UploadPanel';
import * as api from '@/api/runs';

vi.mock('@/api/runs', async (importOriginal) => ({
  ...(await importOriginal()),
  listRuns: vi.fn(async () => []),
  uploadRun: vi.fn(),
}));

function renderApp() {
  return render(
    <ToastProvider>
      <RunsProvider>
        <MemoryRouter initialEntries={['/upload']}>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<HomePanel />} />
              <Route path="/upload" element={<UploadPanel />} />
            </Route>
          </Routes>
        </MemoryRouter>
        <Toast />
      </RunsProvider>
    </ToastProvider>,
  );
}

function csv(name, size = 1024) {
  const file = new File(['a,b\n1,2\n'], name, { type: 'text/csv' });
  Object.defineProperty(file, 'size', { value: size });
  return file;
}

describe('upload flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('rejects a non-csv without calling the API', async () => {
    // applyAccept:false bypasses the input's accept=".csv" filter, mimicking a
    // user who picked "All files" in the OS dialog. The client-side guard is
    // what has to catch it.
    const user = userEvent.setup({ applyAccept: false });
    renderApp();

    await user.upload(screen.getByLabelText(/choose a file/i), csv('notes.xlsx'));

    expect(await screen.findByRole('alert')).toHaveTextContent(/isn.t a CSV/i);
    expect(api.uploadRun).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /start assessment/i })).toBeDisabled();
  });

  it('lands on Home with nothing selected, then toasts when the upload resolves', async () => {
    const user = userEvent.setup();

    // Hold the response open so we can assert the user is already on Home while
    // the request is still in flight.
    let resolveUpload;
    api.uploadRun.mockReturnValue(
      new Promise((resolve) => {
        resolveUpload = resolve;
      }),
    );

    renderApp();
    await user.upload(screen.getByLabelText(/choose a file/i), csv('customers.csv'));
    await user.click(screen.getByRole('button', { name: /start assessment/i }));

    // Navigation happened immediately, without awaiting the upload, and Home
    // defaults to nothing selected.
    expect(await screen.findByText(/select a run to see its report/i)).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();

    // UploadPanel is now unmounted. The toast must still fire, which is the whole
    // reason the mutation lives in UploadProvider at the root.
    resolveUpload({ id: 'run_9', file: 'customers.csv', status: 'processing' });

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        'customers.csv has been received and is being processed',
      ),
    );
    // Still not routed anywhere run-specific.
    expect(screen.getByText(/select a run to see its report/i)).toBeInTheDocument();
  });

  it('reports an upload failure through the toast', async () => {
    const user = userEvent.setup();
    api.uploadRun.mockRejectedValue(new Error('Gateway timeout.'));

    renderApp();
    await user.upload(screen.getByLabelText(/choose a file/i), csv('customers.csv'));
    await user.click(screen.getByRole('button', { name: /start assessment/i }));

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/could not be uploaded/i),
    );
  });
});
