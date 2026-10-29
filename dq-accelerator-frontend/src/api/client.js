// Single place the backend origin is configured. Swapping the MSW mock for the
// real dqa.api service is this one env var, nothing else in the app changes.
export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api';

export function apiUrl(path) {
  return `${API_BASE}${path}`;
}

// The session is an HttpOnly cookie the backend sets at sign-in (see
// api/auth.js). Every request sends it, including across origins -- the dev
// server on :5173 talking to the API on :8000 -- which the backend's CORS
// setup already allows.
const CREDENTIALS = 'include';

// Any 401 means the session has gone: expired, signed out in another tab, or
// never there. AuthProvider subscribes here, so the user lands back on the
// sign-in page from wherever it happened, and no caller has to handle it.
const unauthorizedListeners = new Set();

export function onUnauthorized(listener) {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

// The thrown Error carries `status` and, when the backend sends one, `code`
// (the `errorCode` field) -- revision 5's connect dialog branches on it to
// point the user at the right field. The message is still the user-facing
// `error` string, so every existing catch-and-toast keeps working unchanged.
async function throwIfError(res) {
  if (res.ok) return;
  if (res.status === 401) unauthorizedListeners.forEach((listener) => listener());
  let detail;
  let code;
  try {
    const body = await res.json();
    detail = body?.error ?? body?.detail;
    code = body?.errorCode;
  } catch {
    detail = res.statusText;
  }
  const error = new Error(detail || `Request failed (${res.status})`);
  error.status = res.status;
  error.code = code;
  throw error;
}

export async function fetchJson(path, options) {
  const res = await fetch(apiUrl(path), { credentials: CREDENTIALS, ...options });
  await throwIfError(res);

  // A DELETE typically answers 204 with no body; don't try to parse that.
  if (res.status === 204 || res.headers.get('content-length') === '0') return null;

  return res.json();
}

// Used only where a plain <a download> can't be trusted to reach the mock (see
// downloadReport in api/runs.js) -- fetches the raw response as a Blob instead
// of parsing JSON, and reads the filename off Content-Disposition.
export async function fetchBlob(path) {
  const res = await fetch(apiUrl(path), { credentials: CREDENTIALS });
  await throwIfError(res);

  const disposition = res.headers.get('content-disposition') ?? '';
  const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1];

  return { blob: await res.blob(), filename };
}

// Dev-only download path shared by run and assessment reports: a real fetch()
// (which MSW does intercept) turned into a blob and saved through a temporary
// link. See DownloadMenu for why production uses a plain <a download> instead.
export async function downloadFile(path, fallbackName) {
  const { blob, filename } = await fetchBlob(path);

  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename || fallbackName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Give the browser a moment to pick up the blob before freeing it.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
