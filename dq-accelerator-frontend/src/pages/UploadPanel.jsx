import { useContext, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { RunsContext } from '@/components/RunsProvider';
import { validateCsv } from '@/lib/validateCsv';
import { formatBytes } from '@/lib/format';
import { ACCEPTED_UPLOAD_EXTENSIONS, MAX_UPLOAD_BYTES } from '@/api/constants';

export function UploadPanel() {
  const [file, setFile] = useState(null);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);
  const navigate = useNavigate();
  const { startUpload } = useContext(RunsContext);

  function handleChange(event) {
    const picked = event.target.files?.[0] ?? null;
    if (!picked) return;

    // A light sanity check only -- the backend is authoritative on real
    // limits (size, extension, content). See validateCsv.js.
    const message = validateCsv(picked);
    if (message) {
      setFile(null);
      setError(message);
      clearInput();
      return;
    }

    setError(null);
    setFile(picked);
  }

  function clearInput() {
    // A file input's value can't be set from JS, so reset the DOM node itself --
    // otherwise re-picking the same filename fires no change event.
    if (inputRef.current) inputRef.current.value = '';
  }

  function clearFile() {
    setFile(null);
    setError(null);
    clearInput();
  }

  function handleStart() {
    if (!file) return;
    // Fire and navigate in the same breath. We do NOT await the response: the
    // user lands on Home immediately and the toast confirms receipt later.
    startUpload(file);
    navigate('/');
  }

  return (
    <div className="mx-auto max-w-2xl px-10 py-14">
      <h1 className="text-2xl font-extrabold tracking-tight">New assessment</h1>
      <p className="mt-2 text-sm text-ink-2">
        Upload a CSV, TSV, or text extract. We&rsquo;ll profile it and score it
        across seven data quality dimensions.
      </p>

      <label
        htmlFor="csv-input"
        className="mt-8 flex cursor-pointer flex-col items-center gap-2 rounded-[var(--radius-card)] border-2 border-dashed border-border bg-surface px-6 py-14 text-center transition hover:border-accent/50 hover:bg-accent/2"
      >
        <span
          aria-hidden="true"
          className="grid size-11 place-items-center rounded-full bg-accent/10 text-lg text-accent"
        >
          &uarr;
        </span>
        <span className="mt-1 text-sm font-semibold text-ink">
          Choose a file
        </span>
        <span className="text-xs text-ink-3">
          {ACCEPTED_UPLOAD_EXTENSIONS.join(', ')} &middot; up to {formatBytes(MAX_UPLOAD_BYTES)}
        </span>
        <input
          ref={inputRef}
          id="csv-input"
          type="file"
          accept={ACCEPTED_UPLOAD_EXTENSIONS.join(',')}
          className="sr-only"
          onChange={handleChange}
        />
      </label>

      {error && (
        <p
          role="alert"
          className="mt-4 rounded-[var(--radius-control)] border border-critical/25 bg-critical/6 px-4 py-3 text-sm text-critical"
        >
          {error}
        </p>
      )}

      {file && (
        <div className="mt-4 flex items-center gap-3 rounded-[var(--radius-card)] border border-border bg-surface px-4 py-3.5">
          <div className="min-w-0 flex-1">
            <p className="truncate font-mono text-[13px] text-ink" title={file.name}>
              {file.name}
            </p>
            <p className="mt-0.5 text-xs text-ink-3">{formatBytes(file.size)}</p>
          </div>
          <button
            type="button"
            onClick={clearFile}
            className="rounded-[var(--radius-control)] px-3 py-1.5 text-xs font-semibold text-ink-2 transition hover:bg-ink/5"
          >
            Clear
          </button>
        </div>
      )}

      <button
        type="button"
        onClick={handleStart}
        disabled={!file}
        className="mt-6 w-full rounded-[var(--radius-control)] bg-accent px-5 py-3 text-sm font-semibold text-white transition hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-ink-3/40"
      >
        Start assessment
      </button>

      <p className="mt-3 text-center text-xs text-ink-3">
        You&rsquo;ll go straight back to your runs. No need to wait here.
      </p>
    </div>
  );
}
