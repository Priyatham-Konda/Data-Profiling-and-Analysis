import { ACCEPTED_UPLOAD_EXTENSIONS, MAX_UPLOAD_BYTES } from '@/api/constants';
import { formatBytes } from './format';

/**
 * A light, non-blocking sanity check -- NOT a re-implementation of backend
 * validation. Per API_CONTRACT.md, the real limits (size, extension,
 * content) are enforced authoritatively by the server, which returns
 * 413/415/400 with a message already written for a human; that response
 * surfaces through the normal upload-failure toast (see useUpload in
 * RunsProvider.jsx). This only catches the two things worth telling the user
 * about before they wait on a network round trip: nothing was picked, or the
 * extension is obviously wrong. Everything else -- exact size ceilings,
 * "no parseable header", row counts, sampling -- is the backend's call, not
 * duplicated here.
 */
export function validateCsv(file) {
  if (!file) return 'Choose a file to continue.';

  const hasAcceptedExtension = ACCEPTED_UPLOAD_EXTENSIONS.some((ext) =>
    file.name.toLowerCase().endsWith(ext),
  );
  if (!hasAcceptedExtension) {
    return `That file isn’t a CSV. Upload a ${ACCEPTED_UPLOAD_EXTENSIONS.join(
      ', ',
    )} file to run an assessment.`;
  }

  // A generous ceiling purely to avoid attempting a doomed multi-hundred-MB
  // upload over a slow connection -- the server's real limit (and the exact
  // message for exceeding it) is authoritative, this is just an early out.
  if (file.size > MAX_UPLOAD_BYTES) {
    return `That file is ${formatBytes(file.size)}, above the ${formatBytes(
      MAX_UPLOAD_BYTES,
    )} limit. Split the export into smaller files, or export a subset of columns.`;
  }

  return null;
}
