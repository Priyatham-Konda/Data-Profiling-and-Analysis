import { useContext, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { API_BASE } from '@/api/client';
import { MAX_ASSESSMENT_OBJECTS } from '@/api/constants';
import {
  createAssessment,
  createConnection,
  deleteConnection,
  listObjects,
} from '@/api/salesforce';
import { RunsContext } from '@/components/RunsProvider';
import { ToastContext } from '@/components/Toast';
import { formatCount } from '@/lib/format';

// Revision 5: connect to a Salesforce org with a Connected App's client
// credentials, pick objects, start one assessment covering them all.
//
// The client secret is sensitive. It lives only in this component's state
// while typed, is sent once in the POST body, and is cleared after every
// submit whether it worked or not. Never in the URL, storage, logs or toasts.

// Which inputs an errorCode points at, so the right field is highlighted.
const ERROR_FIELDS = {
  invalid_url: ['instanceUrl'],
  unreachable: ['instanceUrl'],
  invalid_client: ['clientId', 'clientSecret'],
};

// Plain http is only acceptable to a backend on this machine; anywhere else
// the secret would cross the network unencrypted.
function isInsecureBackend() {
  try {
    const url = new URL(API_BASE, window.location.href);
    return url.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  } catch {
    return false;
  }
}

const inputClass = (hasError) =>
  `mt-1.5 w-full rounded-[var(--radius-control)] border bg-surface px-3 py-2 text-sm text-ink outline-none transition focus:border-accent ${
    hasError ? 'border-critical' : 'border-border'
  }`;

function SetupChecklist() {
  return (
    <details className="mt-6 rounded-[var(--radius-card)] border border-border bg-surface px-5 py-4 text-sm text-ink-2">
      <summary className="cursor-pointer font-semibold text-ink">
        What your Salesforce administrator needs to set up
      </summary>
      <ol className="mt-3 list-decimal space-y-1.5 pl-5">
        <li>
          Setup &rarr; App Manager &rarr; New External Client App, with Distribution State
          Local.
        </li>
        <li>
          Enable OAuth Settings. Any callback URL will do (https://localhost/callback); this
          flow never uses it.
        </li>
        <li>Select the scope &ldquo;Manage user data via APIs (api)&rdquo; only.</li>
        <li>Under Flow Enablement, tick Enable Client Credentials Flow, then save.</li>
        <li>
          On the Policies tab, tick Enable Client Credentials Flow again and set the Run As
          (Username).
        </li>
        <li>Under Settings &rarr; OAuth Settings, copy the Consumer Key and Secret.</li>
        <li>The Salesforce address is under Setup &rarr; My Domain.</li>
      </ol>
      <p className="mt-3 text-xs leading-relaxed">
        Choose the Run As user with care: Salesforce has no read-only scope, so the app can do
        whatever that user can. We only ever read, but a dedicated integration user with read
        access and no edit rights guarantees it. Only what that user can see is assessed. New
        credentials can take a few minutes to start working.
      </p>
    </details>
  );
}

function ConnectForm({ onConnected }) {
  const [instanceUrl, setInstanceUrl] = useState('');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [error, setError] = useState(null);
  const [isConnecting, setIsConnecting] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setIsConnecting(true);
    setError(null);
    try {
      const connection = await createConnection({ instanceUrl, clientId, clientSecret });
      onConnected(connection);
    } catch (err) {
      setError({ code: err.code, message: err.message });
    } finally {
      // Success or failure, the secret doesn't outlive the request.
      setClientSecret('');
      setIsConnecting(false);
    }
  }

  const highlighted = new Set(ERROR_FIELDS[error?.code] ?? []);

  return (
    <form onSubmit={handleSubmit} autoComplete="off" className="mt-8 space-y-4">
      <label className="block text-sm font-semibold text-ink">
        Salesforce address
        <input
          type="text"
          value={instanceUrl}
          onChange={(e) => setInstanceUrl(e.target.value)}
          placeholder="acme.my.salesforce.com"
          required
          aria-invalid={highlighted.has('instanceUrl')}
          className={inputClass(highlighted.has('instanceUrl'))}
        />
        <span className="mt-1 block text-xs font-normal text-ink-3">
          Copy it from your browser&rsquo;s address bar while signed in to Salesforce.
        </span>
      </label>
      <label className="block text-sm font-semibold text-ink">
        Client ID
        <input
          type="text"
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
          required
          spellCheck={false}
          aria-invalid={highlighted.has('clientId')}
          className={`${inputClass(highlighted.has('clientId'))} font-mono`}
        />
      </label>
      <label className="block text-sm font-semibold text-ink">
        Client secret
        <input
          type="password"
          value={clientSecret}
          onChange={(e) => setClientSecret(e.target.value)}
          required
          autoComplete="off"
          aria-invalid={highlighted.has('clientSecret')}
          className={`${inputClass(highlighted.has('clientSecret'))} font-mono`}
        />
        <span className="mt-1 block text-xs font-normal text-ink-3">
          Used once to connect and never stored. You&rsquo;ll need to enter it again next time.
        </span>
      </label>

      {error && (
        <p role="alert" className="rounded-[var(--radius-control)] border border-critical/25 bg-critical/6 px-4 py-3 text-sm text-ink-2">
          {error.message}
        </p>
      )}

      <button
        type="submit"
        disabled={isConnecting}
        className="rounded-[var(--radius-control)] bg-accent px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-accent-hover disabled:cursor-wait disabled:opacity-60"
      >
        {isConnecting ? 'Connecting…' : 'Connect'}
      </button>
    </form>
  );
}

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'standard', label: 'Standard' },
  { key: 'custom', label: 'Custom' },
];

function ObjectPicker({ connection, onAssess, isStarting }) {
  const [includeAll, setIncludeAll] = useState(false);
  const [catalog, setCatalog] = useState({ includeAll: null, data: null, error: null });
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [picked, setPicked] = useState(() => new Set());

  useEffect(() => {
    let cancelled = false;
    listObjects(connection.id, { includeAll }).then(
      (data) => {
        if (!cancelled) setCatalog({ includeAll, data, error: null });
      },
      (error) => {
        if (!cancelled) setCatalog({ includeAll, data: null, error });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [connection.id, includeAll]);

  const isCurrent = catalog.includeAll === includeAll;
  const data = isCurrent ? catalog.data : null;
  const error = isCurrent ? catalog.error : null;

  const needle = query.trim().toLowerCase();
  const visible = (data?.objects ?? []).filter((object) => {
    if (filter === 'standard' && object.custom) return false;
    if (filter === 'custom' && !object.custom) return false;
    if (!needle) return true;
    return object.label.toLowerCase().includes(needle) || object.name.toLowerCase().includes(needle);
  });

  const atLimit = picked.size >= MAX_ASSESSMENT_OBJECTS;

  function toggle(name) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else if (next.size < MAX_ASSESSMENT_OBJECTS) next.add(name);
      return next;
    });
  }

  return (
    <section className="mt-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-ink">Choose objects to assess</h2>
        <p className="text-xs text-ink-3">
          {picked.size} selected &middot; up to {MAX_ASSESSMENT_OBJECTS}
        </p>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search objects"
          aria-label="Search objects"
          className="min-w-48 flex-1 rounded-[var(--radius-control)] border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
        />
        <div className="flex gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              aria-pressed={filter === f.key}
              onClick={() => setFilter(f.key)}
              className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                filter === f.key ? 'bg-accent text-white' : 'bg-ink/5 text-ink-2 hover:bg-ink/8'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-4 rounded-[var(--radius-card)] border border-border bg-surface">
        {!data && !error && <p className="px-5 py-4 text-sm text-ink-3">Loading objects&hellip;</p>}
        {error && <p className="px-5 py-4 text-sm text-critical">{error.message}</p>}
        {data && visible.length === 0 && (
          <p className="px-5 py-4 text-sm text-ink-3">No objects match.</p>
        )}
        {data && visible.length > 0 && (
          <ul className="max-h-96 divide-y divide-border overflow-y-auto">
            {visible.map((object) => {
              const checked = picked.has(object.name);
              return (
                <li key={object.name}>
                  <label className="flex cursor-pointer items-center gap-3 px-5 py-2.5 text-sm hover:bg-ink/3">
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!checked && atLimit}
                      onChange={() => toggle(object.name)}
                      className="size-3.5 accent-accent"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="text-ink">{object.label}</span>{' '}
                      <span className="font-mono text-[11px] text-ink-3">{object.name}</span>
                    </span>
                    {object.suggested && (
                      <span className="rounded-full bg-accent/10 px-1.5 py-0.5 text-[10px] font-semibold text-accent">
                        Suggested
                      </span>
                    )}
                    <span className="w-32 text-right text-xs text-ink-3">
                      {object.recordCount == null
                        ? 'count unavailable'
                        : `about ${formatCount(object.recordCount)} records`}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <label className="mt-3 flex items-center gap-2 text-xs text-ink-2">
        <input
          type="checkbox"
          checked={includeAll}
          onChange={(e) => setIncludeAll(e.target.checked)}
          className="size-3.5 accent-accent"
        />
        Show system objects
        {!includeAll && data?.hiddenCount > 0 && (
          <span className="text-ink-3">({formatCount(data.hiddenCount)} hidden)</span>
        )}
      </label>

      <button
        type="button"
        onClick={() => onAssess([...picked])}
        disabled={picked.size === 0 || isStarting}
        className="mt-6 rounded-[var(--radius-control)] bg-accent px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isStarting && 'Starting…'}
        {!isStarting && picked.size === 0 && 'Assess objects'}
        {!isStarting && picked.size > 0 && `Assess ${picked.size} object${picked.size === 1 ? '' : 's'}`}
      </button>
    </section>
  );
}

export function SalesforcePanel() {
  const navigate = useNavigate();
  const { refreshAssessments } = useContext(RunsContext);
  const { showToast } = useContext(ToastContext);
  const [connection, setConnection] = useState(null);
  const [isStarting, setIsStarting] = useState(false);

  // A connection nothing was assessed with is closed when the user leaves, so
  // the backend can discard the secret straight away rather than after the
  // 30-minute idle timeout. Best effort: a failure here changes nothing.
  const openConnectionId = useRef(null);
  useEffect(() => {
    return () => {
      if (openConnectionId.current) deleteConnection(openConnectionId.current).catch(() => {});
    };
  }, []);

  function handleConnected(next) {
    openConnectionId.current = next.id;
    setConnection(next);
  }

  function handleDifferentOrg() {
    const id = connection.id;
    openConnectionId.current = null;
    setConnection(null);
    deleteConnection(id).catch(() => {});
  }

  async function handleAssess(objects) {
    setIsStarting(true);
    try {
      const assessment = await createAssessment(connection.id, objects);
      // The connection now belongs to the assessment; don't close it on leave.
      openConnectionId.current = null;
      showToast(`${assessment.name}: assessing ${objects.length} object${objects.length === 1 ? '' : 's'}.`);
      refreshAssessments();
      navigate(`/?assessment=${encodeURIComponent(assessment.id)}`);
    } catch (err) {
      showToast(`Could not start the assessment. ${err.message}`, 'error');
      setIsStarting(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-10 py-14">
      <h1 className="text-2xl font-extrabold tracking-tight">Connect Salesforce</h1>
      <p className="mt-2 text-sm text-ink-2">
        Assess the data in your Salesforce org directly. We read the objects you choose, score
        each one across seven data quality dimensions, and give an overall score for the org.
      </p>

      {isInsecureBackend() && (
        <p className="mt-5 rounded-[var(--radius-control)] border border-warn/30 bg-warn/8 px-4 py-3 text-xs leading-relaxed text-ink-2">
          This app talks to its backend over plain http, so the client secret would travel
          unencrypted. Use an https backend address before connecting a real org.
        </p>
      )}

      {connection ? (
        <>
          <div className="mt-8 flex flex-wrap items-center justify-between gap-3 rounded-[var(--radius-card)] border border-healthy/30 bg-healthy/6 px-5 py-4">
            <p className="flex flex-wrap items-center gap-2 text-sm text-ink">
              <span aria-hidden="true" className="size-2 rounded-full bg-healthy" />
              Connected to <strong>{connection.orgName}</strong> ({connection.orgEdition}) as{' '}
              <span className="font-mono text-xs">{connection.username}</span>
              {connection.environment === 'sandbox' && (
                <span className="rounded-full bg-warn/12 px-2 py-0.5 text-[10px] font-semibold text-warn">
                  Sandbox
                </span>
              )}
            </p>
            <button
              type="button"
              onClick={handleDifferentOrg}
              className="text-xs font-semibold text-accent transition hover:text-accent-hover"
            >
              Use a different org
            </button>
          </div>
          <ObjectPicker connection={connection} onAssess={handleAssess} isStarting={isStarting} />
        </>
      ) : (
        <>
          <ConnectForm onConnected={handleConnected} />
          <SetupChecklist />
        </>
      )}
    </div>
  );
}
