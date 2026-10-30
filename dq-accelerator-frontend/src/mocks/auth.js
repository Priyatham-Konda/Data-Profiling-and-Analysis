// Revision 6 in-memory stand-in for the /auth endpoints. Like db.js and
// salesforce.js, the rules are plain functions returning a result or
// { rejection: { status, error } }, so handlers.js stays a thin HTTP wrapper
// and the rules are testable without going through fetch.
//
// Always available, so there is something to sign in with straight away:
//   demo@dataskate.ai / dataskate123
//
// Accounts live in memory and vanish on reload, like every mock run. The
// session cookie carries the user record itself, so whoever is signed in
// stays signed in across reloads. The real backend's cookie is an opaque
// HttpOnly token; this one has to be readable because MSW sets it through
// document.cookie.
//
// Not mocked: per-user ownership. Every mock account sees the same runs and
// assessments. The real backend keeps them private (API_CONTRACT.md,
// revision 6).

export const MOCK_SESSION_COOKIE = 'dqa_mock_session';
export const DEMO_ACCOUNT = Object.freeze({
  name: 'Demo User',
  email: 'demo@dataskate.ai',
  password: 'dataskate123',
});

const PASSWORD_MIN_LENGTH = 8;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

const users = new Map([
  [DEMO_ACCOUNT.email, { id: 'usr_demo', ...DEMO_ACCOUNT }],
]);

function publicUser({ id, name, email }) {
  return { id, name, email };
}

export function signUp({ name = '', email = '', password = '' } = {}) {
  const cleanName = name.trim();
  const cleanEmail = email.trim().toLowerCase();
  if (!cleanName) return { rejection: { status: 400, error: 'Enter your name.' } };
  if (!EMAIL.test(cleanEmail)) {
    return { rejection: { status: 400, error: 'Enter a valid email address.' } };
  }
  if (password.length < PASSWORD_MIN_LENGTH) {
    return {
      rejection: {
        status: 400,
        error: `Use a password of at least ${PASSWORD_MIN_LENGTH} characters.`,
      },
    };
  }
  if (users.has(cleanEmail)) {
    return {
      rejection: {
        status: 409,
        error: 'An account with this email already exists. Sign in instead.',
      },
    };
  }

  const user = {
    id: `usr_${Math.random().toString(36).slice(2, 14)}`,
    name: cleanName,
    email: cleanEmail,
    password,
  };
  users.set(cleanEmail, user);
  return { user: publicUser(user) };
}

export function signIn({ email = '', password = '' } = {}) {
  const user = users.get(email.trim().toLowerCase());
  if (!user || user.password !== password) {
    return {
      rejection: { status: 401, error: "That email and password don't match an account." },
    };
  }
  return { user: publicUser(user) };
}

export function sessionCookie(user) {
  return `${MOCK_SESSION_COOKIE}=${encodeURIComponent(JSON.stringify(user))}; Path=/; SameSite=Lax`;
}

export function clearedSessionCookie() {
  return `${MOCK_SESSION_COOKIE}=; Path=/; Max-Age=0`;
}

// MSW hands resolvers their cookies already URI-decoded; the fallback covers
// a value that arrives still encoded.
export function userFromSession(value) {
  if (!value) return null;
  for (const text of [value, safeDecode(value)]) {
    try {
      const user = JSON.parse(text);
      if (user?.id && user?.email) return publicUser(user);
    } catch {
      // Try the next form.
    }
  }
  return null;
}

function safeDecode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}
