import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ToastProvider } from '@/components/Toast';
import { AuthProvider, COOKIE_REFUSED, RequireAuth } from '@/components/AuthProvider';
import { AuthPage } from './AuthPage';
import * as authApi from '@/api/auth';
import * as runsApi from '@/api/runs';

vi.mock('@/api/auth', () => ({
  getCurrentUser: vi.fn(),
  signIn: vi.fn(),
  signUp: vi.fn(),
  signOut: vi.fn(),
}));

vi.mock('@/api/runs', async (importOriginal) => ({
  ...(await importOriginal()),
  listRuns: vi.fn().mockResolvedValue([]),
}));

vi.mock('@/api/salesforce', async (importOriginal) => ({
  ...(await importOriginal()),
  listAssessments: vi.fn().mockResolvedValue([]),
}));

const ALICE = { id: 'usr_alice', name: 'Alice Smith', email: 'alice@example.com' };

function renderApp(initialEntry = '/') {
  return render(
    <ToastProvider>
      <AuthProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="/login" element={<AuthPage mode="login" />} />
            <Route path="/signup" element={<AuthPage mode="signup" />} />
            <Route element={<RequireAuth />}>
              <Route path="/" element={<p>Home panel</p>} />
              <Route path="/upload" element={<p>Upload panel</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </ToastProvider>,
  );
}

describe('signing in', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('sends a signed-out visitor to sign-in, without loading any runs', async () => {
    authApi.getCurrentUser.mockResolvedValue(null);
    renderApp('/');

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.getByAltText('dataskate')).toBeInTheDocument();
    expect(runsApi.listRuns).not.toHaveBeenCalled();
  });

  it('goes straight in when a session already exists', async () => {
    authApi.getCurrentUser.mockResolvedValue(ALICE);
    renderApp('/');

    expect(await screen.findByText('Home panel')).toBeInTheDocument();
    expect(runsApi.listRuns).toHaveBeenCalled();
  });

  it('returns to the page that asked for sign-in', async () => {
    // Signed out at startup; after sign-in, the session check finds the user.
    authApi.getCurrentUser.mockResolvedValueOnce(null).mockResolvedValue(ALICE);
    authApi.signIn.mockResolvedValue(ALICE);
    const user = userEvent.setup();
    renderApp('/upload');

    await user.type(await screen.findByLabelText('Email'), 'alice@example.com');
    await user.type(screen.getByLabelText('Password'), 'correct horse');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByText('Upload panel')).toBeInTheDocument();
    expect(authApi.signIn).toHaveBeenCalledWith({
      email: 'alice@example.com',
      password: 'correct horse',
    });
  });

  it('shows the server message and clears the password when sign-in fails', async () => {
    authApi.getCurrentUser.mockResolvedValue(null);
    authApi.signIn.mockRejectedValue(new Error("That email and password don't match an account."));
    const user = userEvent.setup();
    renderApp('/login');

    await user.type(await screen.findByLabelText('Email'), 'alice@example.com');
    await user.type(screen.getByLabelText('Password'), 'wrong-password');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByRole('alert')).toHaveTextContent("don't match an account");
    expect(screen.getByLabelText('Password')).toHaveValue('');
  });

  it('explains when the browser refuses the session cookie', async () => {
    // Sign-in succeeds, but the very next request still arrives signed out --
    // what a cross-site app and API look like from in here.
    authApi.getCurrentUser.mockResolvedValue(null);
    authApi.signIn.mockResolvedValue(ALICE);
    const user = userEvent.setup();
    renderApp('/login');

    await user.type(await screen.findByLabelText('Email'), 'alice@example.com');
    await user.type(screen.getByLabelText('Password'), 'correct horse');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(COOKIE_REFUSED);
    expect(screen.queryByText('Home panel')).not.toBeInTheDocument();
  });

  it('creates an account from the sign-up page', async () => {
    authApi.getCurrentUser.mockResolvedValueOnce(null).mockResolvedValue(ALICE);
    authApi.signUp.mockResolvedValue(ALICE);
    const user = userEvent.setup();
    renderApp('/login');

    await user.click(await screen.findByRole('link', { name: 'Create an account' }));
    await user.type(screen.getByLabelText('Name'), 'Alice Smith');
    await user.type(screen.getByLabelText('Email'), 'alice@example.com');
    // The hint sits inside the label, so it is part of the accessible name.
    await user.type(screen.getByLabelText(/^Password/), 'correct horse');
    await user.click(screen.getByRole('button', { name: 'Create account' }));

    expect(await screen.findByText('Home panel')).toBeInTheDocument();
    expect(authApi.signUp).toHaveBeenCalledWith({
      name: 'Alice Smith',
      email: 'alice@example.com',
      password: 'correct horse',
    });
  });
});

describe('a session ending mid-use', () => {
  it('returns to sign-in on any 401', async () => {
    authApi.getCurrentUser.mockResolvedValue(ALICE);
    renderApp('/');
    expect(await screen.findByText('Home panel')).toBeInTheDocument();

    // The real client.js path: a 401 from any endpoint notifies listeners.
    const { fetchJson } = await import('@/api/client');
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ error: 'Sign in to continue.' }), { status: 401 }));
    await act(async () => {
      await fetchJson('/runs').catch(() => {});
    });
    fetchSpy.mockRestore();

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument());
  });
});
