import { useContext, useState } from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';
import { AuthContext } from '@/components/AuthProvider';
import logoUrl from '@/assets/dataskate-logo.png';

// Mirrors config.PASSWORD_MIN_LENGTH on the backend, which is authoritative;
// this only lets the browser say so before a round trip.
const PASSWORD_MIN_LENGTH = 8;

const COPY = {
  login: {
    title: 'Sign in',
    lead: 'Welcome back. Sign in to see your assessments.',
    submit: 'Sign in',
    pending: 'Signing in…',
    switchPrompt: 'New to DQ Accelerator?',
    switchLabel: 'Create an account',
    switchTo: '/signup',
  },
  signup: {
    title: 'Create your account',
    lead: 'Your uploads, Salesforce assessments and reports stay private to you.',
    submit: 'Create account',
    pending: 'Creating account…',
    switchPrompt: 'Already have an account?',
    switchLabel: 'Sign in',
    switchTo: '/login',
  },
};

const inputClass =
  'mt-1.5 w-full rounded-[var(--radius-control)] border border-border bg-surface px-3 py-2 text-sm font-normal text-ink outline-none transition focus:border-accent';

/**
 * /login and /signup: one form, two modes. Signing up also signs in, so both
 * end the same way -- AuthContext gets a user, and the redirect below sends
 * them on to wherever RequireAuth turned them away from (Home by default).
 */
export function AuthPage({ mode }) {
  const { user, signIn, signUp } = useContext(AuthContext);
  const location = useLocation();
  const copy = COPY[mode];
  const isSignup = mode === 'signup';

  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (user) {
    const from = location.state?.from;
    const to = from ? `${from.pathname}${from.search ?? ''}` : '/';
    return <Navigate to={to} replace />;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setIsSubmitting(true);
    setError(null);
    try {
      await (isSignup ? signUp({ name, email, password }) : signIn({ email, password }));
    } catch (err) {
      setError(err.message);
      // Kept on failure only for sign-up, where the likely mistake is
      // elsewhere (a taken email); a failed sign-in usually means retyping it.
      if (!isSignup) setPassword('');
      setIsSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center">
          <img src={logoUrl} alt="dataskate" className="h-7 w-auto" />
          <p className="mt-3 text-[11px] font-semibold tracking-[0.08em] text-ink-3 uppercase">
            DQ Accelerator
          </p>
        </div>

        <div className="rounded-[var(--radius-card)] border border-border bg-surface px-7 py-8 shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
          <h1 className="text-xl font-extrabold tracking-tight">{copy.title}</h1>
          <p className="mt-1.5 text-sm text-ink-2">{copy.lead}</p>

          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            {isSignup && (
              <label className="block text-sm font-semibold text-ink">
                Name
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                  required
                  className={inputClass}
                />
              </label>
            )}
            <label className="block text-sm font-semibold text-ink">
              Email
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                required
                className={inputClass}
              />
            </label>
            <label className="block text-sm font-semibold text-ink">
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={isSignup ? 'new-password' : 'current-password'}
                minLength={isSignup ? PASSWORD_MIN_LENGTH : undefined}
                required
                className={inputClass}
              />
              {isSignup && (
                <span className="mt-1 block text-xs font-normal text-ink-3">
                  At least {PASSWORD_MIN_LENGTH} characters.
                </span>
              )}
            </label>

            {error && (
              <p
                role="alert"
                className="rounded-[var(--radius-control)] border border-critical/25 bg-critical/6 px-4 py-3 text-sm text-ink-2"
              >
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={isSubmitting}
              className="w-full rounded-[var(--radius-control)] bg-accent px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-accent-hover disabled:cursor-wait disabled:opacity-60"
            >
              {isSubmitting ? copy.pending : copy.submit}
            </button>
          </form>
        </div>

        <p className="mt-6 text-center text-sm text-ink-2">
          {copy.switchPrompt}{' '}
          <Link
            to={copy.switchTo}
            state={location.state}
            className="font-semibold text-accent hover:text-accent-hover"
          >
            {copy.switchLabel}
          </Link>
        </p>
      </div>
    </div>
  );
}
