import { fetchJson } from './client';

/**
 * Accounts (revision 6). Payload shapes:
 *
 *   POST /auth/signup  { name, email, password }  -> 201 user, sets the session cookie
 *        400 blank name / invalid email / password under 8 characters
 *        409 email already registered
 *   POST /auth/login   { email, password }        -> 200 user, sets the session cookie
 *        401 email and password don't match an account
 *   POST /auth/logout                             -> 204, always
 *   GET  /auth/me                                 -> 200 user, 401 when signed out
 *
 *   user: { id, name, email }
 *
 * The session lives only in an HttpOnly cookie, which this code never sees.
 * Nothing about it goes in localStorage or sessionStorage -- the same rule
 * as the Salesforce secret, for the same reason.
 */

const JSON_HEADERS = { 'Content-Type': 'application/json' };

// null when signed out, rather than a rejection: "nobody is signed in" is an
// ordinary answer at startup, not an error.
export async function getCurrentUser() {
  try {
    return await fetchJson('/auth/me');
  } catch (error) {
    if (error.status === 401) return null;
    throw error;
  }
}

export function signIn({ email, password }) {
  return fetchJson('/auth/login', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ email, password }),
  });
}

export function signUp({ name, email, password }) {
  return fetchJson('/auth/signup', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ name, email, password }),
  });
}

export function signOut() {
  return fetchJson('/auth/logout', { method: 'POST' });
}
