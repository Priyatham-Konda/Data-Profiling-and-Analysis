// Single place the backend origin is configured. Swapping the MSW mock for the
// real dqa.api service is this one env var, nothing else in the app changes.
export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api';

export function apiUrl(path) {
  return `${API_BASE}${path}`;
}

async function throwIfError(res) {
  if (res.ok) return;
  let detail;
  try {
    const body = await res.json();
    detail = body?.error ?? body?.detail;
  } catch {
    detail = res.statusText;
  }
  throw new Error(detail || `Request failed (${res.status})`);
}

export async function fetchJson(path, options) {
  const res = await fetch(apiUrl(path), options);
  await throwIfError(res);

  // A DELETE typically answers 204 with no body; don't try to parse that.
  if (res.status === 204 || res.headers.get('content-length') === '0') return null;

  return res.json();
}

// Used only where a plain <a download> can't be trusted to reach the mock (see
// downloadReport in api/runs.js) -- fetches the raw response as a Blob instead
// of parsing JSON, and reads the filename off Content-Disposition.
export async function fetchBlob(path) {
  const res = await fetch(apiUrl(path));
  await throwIfError(res);

  const disposition = res.headers.get('content-disposition') ?? '';
  const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1];

  return { blob: await res.blob(), filename };
}
