import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import * as api from '@/api/auth';
import { onUnauthorized } from '@/api/client';
import { RunsProvider } from './RunsProvider';

export const AuthContext = createContext(null);

export const COOKIE_REFUSED =
  'Signed in, but this browser did not keep the session. Open this app from the same ' +
  'host name as VITE_API_BASE_URL -- both localhost, or both the same IP address -- and ' +
  'try again.';

// The session cookie is HttpOnly, so this code can't see whether the browser
// stored it. The only proof is asking the server. A browser refuses it when
// the app and the API are on different sites -- the app opened at
// http://192.168.x.x:5173 while VITE_API_BASE_URL says localhost, say --
// and without this check that looks like a sign-in that silently bounces
// straight back to the sign-in page.
async function confirmSession() {
  const user = await api.getCurrentUser();
  if (!user) throw new Error(COOKIE_REFUSED);
  return user;
}

/**
 * Who is signed in. `user` is undefined while the first GET /auth/me is in
 * flight, null when signed out, and { id, name, email } otherwise. The
 * undefined state is what stops a signed-in user from seeing the sign-in
 * page flash on every reload.
 */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(undefined);

  useEffect(() => {
    let cancelled = false;
    // A failure other than 401 (the API is down, say) also lands on the
    // sign-in page, which is where the error will surface when they try.
    api.getCurrentUser().then(
      (current) => {
        if (!cancelled) setUser(current);
      },
      () => {
        if (!cancelled) setUser(null);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  // A session that ends mid-use: any 401 from any request signs the app out.
  useEffect(() => onUnauthorized(() => setUser(null)), []);

  const signIn = useCallback(async (credentials) => {
    await api.signIn(credentials);
    setUser(await confirmSession());
  }, []);

  const signUp = useCallback(async (details) => {
    await api.signUp(details);
    setUser(await confirmSession());
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.signOut();
    } finally {
      // Signed out locally even if the request failed: the user asked to
      // leave, and the server session expires on its own.
      setUser(null);
    }
  }, []);

  const value = useMemo(
    () => ({ user, isChecking: user === undefined, signIn, signUp, signOut }),
    [user, signIn, signUp, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/**
 * Layout route around everything that needs a signed-in user. RunsProvider
 * lives here rather than at the app root so that nothing polls /runs before
 * sign-in, and so that signing out unmounts it -- the next account to sign in
 * on this browser starts from an empty list, never the previous user's.
 */
export function RequireAuth() {
  const { user, isChecking } = useContext(AuthContext);
  const location = useLocation();

  if (isChecking) return null;
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />;

  return (
    <RunsProvider>
      <Outlet />
    </RunsProvider>
  );
}
