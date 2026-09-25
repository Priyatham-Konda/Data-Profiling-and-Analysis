import { useEffect, useRef, useState } from 'react';
import { getDimension, getRuleExamples } from '@/api/runs';
import { dimensionLabel } from '@/api/constants';
import { bandPill, bandLabel, bandText } from '@/lib/band';
import { formatCount, formatPercent, formatScore } from '@/lib/format';

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Neutral shades rather than band colours (healthy/warn/critical) on purpose
// -- severity is about how much a rule weighs in the dimension score, not
// about whether it's passing, so it shouldn't visually compete with pass rate.
const SEVERITY_TEXT = {
  high: 'text-ink',
  medium: 'text-ink-2',
  low: 'text-ink-3',
};

// RuleExamplesOverlay is only ever opened from the rule list below, so it
// stays private to this file rather than having its own.
//
// It manages its own Escape/Tab handling with a plain onKeyDown prop instead
// of a document listener, on purpose: focus is trapped inside this panel, so
// the keydown always bubbles up from a focused element inside it, through
// this div, and stopPropagation() here means it never reaches the drawer's
// own document listener underneath. Without that, pressing Escape once would
// close both layers at the same time instead of just this one.
function RuleExamplesOverlay({ runId, dimensionKey, rule, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const panelRef = useRef(null);
  const backRef = useRef(null);
  const openerRef = useRef(null);

  // Lazy fetch: this request only exists because a rule was opened, not
  // preloaded alongside the rest of the dimension.
  useEffect(() => {
    let cancelled = false;

    getRuleExamples(runId, dimensionKey, rule.id).then(
      (result) => {
        if (!cancelled) setData(result);
      },
      (err) => {
        if (!cancelled) setError(err);
      },
    );

    return () => {
      cancelled = true;
    };
  }, [runId, dimensionKey, rule.id]);

  useEffect(() => {
    openerRef.current = document.activeElement;
    backRef.current?.focus();
    return () => openerRef.current?.focus?.();
  }, []);

  function handleKeyDown(event) {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;

    const items = panelRef.current?.querySelectorAll(FOCUSABLE);
    if (!items?.length) return;
    const first = items[0];
    const last = items[items.length - 1];

    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  const isPending = !data && !error;

  return (
    <div
      ref={panelRef}
      role="dialog"
      aria-modal="true"
      aria-label={`${rule.name} examples`}
      onKeyDown={handleKeyDown}
      className="animate-fade-in fixed inset-0 z-[60] flex flex-col bg-surface"
    >
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-border px-6 py-5 md:px-10">
        <div>
          <button
            ref={backRef}
            type="button"
            onClick={onClose}
            className="flex items-center gap-1.5 text-sm font-semibold text-accent transition hover:text-accent-hover"
          >
            <span aria-hidden="true">&larr;</span>
            Back to {dimensionLabel(dimensionKey)}
          </button>
          <h2 className="mt-3 text-lg font-bold tracking-tight">{rule.name}</h2>
          <p className="mt-0.5 font-mono text-xs text-ink-3">
            {rule.id}
            {rule.column && <span className="font-sans"> &middot; {rule.column}</span>}
          </p>
        </div>
        <div className="flex items-start gap-6">
          <div className="text-right">
            <p className="text-xs text-ink-3">Evaluated</p>
            <p className="font-mono text-2xl font-semibold text-ink">
              {formatCount(rule.evaluated)}
            </p>
          </div>
          <div className="text-right">
            <p className="text-xs text-ink-3">Pass rate</p>
            <p className={`font-mono text-2xl font-semibold ${bandText(rule.passRate * 100)}`}>
              {formatPercent(rule.passRate)}
            </p>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6 md:px-10">
        {isPending && <p className="text-sm text-ink-3">Loading examples&hellip;</p>}

        {error && (
          <p className="text-sm text-critical">
            {error?.message ?? 'Could not load examples for this rule.'}
          </p>
        )}

        {data && (
          <>
            <p className="text-sm text-ink-2">
              Showing <span className="font-mono">{data.examples.length}</span> of{' '}
              <span className="font-mono">{formatCount(data.total)}</span> failing records.
            </p>

            <table className="mt-4 w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-ink-3">
                  <th scope="col" className="pb-2 pr-6 font-medium">
                    Row
                  </th>
                  <th scope="col" className="pb-2 pr-6 font-medium">
                    Column
                  </th>
                  <th scope="col" className="pb-2 pr-6 font-medium">
                    Value
                  </th>
                  <th scope="col" className="pb-2 font-medium">
                    Reason
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.examples.map((example) => (
                  <tr
                    key={`${example.row}-${example.column}`}
                    className="border-b border-border/70"
                  >
                    <td className="py-3 pr-6 font-mono text-ink-3">{formatCount(example.row)}</td>
                    <td className="py-3 pr-6 font-mono text-ink">{example.column}</td>
                    <td className="py-3 pr-6 font-mono text-critical">{example.value}</td>
                    <td className="py-3 text-ink-2">{example.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}

export function DimensionDrawer({ runId, dimensionKey, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [openRule, setOpenRule] = useState(null);
  const panelRef = useRef(null);
  const closeRef = useRef(null);
  const openerRef = useRef(null);

  // Lazy fetch: this request only exists because a tile was opened, so it runs
  // on mount rather than alongside the rest of the run.
  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const detail = await getDimension(runId, dimensionKey);
        if (!cancelled) setData(detail);
      } catch (err) {
        if (!cancelled) setError(err);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [runId, dimensionKey]);

  // Modal behaviour: focus in, trap Tab, close on Escape, hand focus back.
  useEffect(() => {
    openerRef.current = document.activeElement;
    closeRef.current?.focus();

    function handleKeyDown(event) {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;

      const items = panelRef.current?.querySelectorAll(FOCUSABLE);
      if (!items?.length) return;
      const first = items[0];
      const last = items[items.length - 1];

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener('keydown', handleKeyDown);
    // Without this cleanup a dismissed drawer would keep eating Escape.
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      openerRef.current?.focus?.();
    };
  }, [onClose]);

  const isPending = !data && !error;

  const label = dimensionLabel(dimensionKey);

  return (
    <>
      <div className="fixed inset-0 z-40">
        <div
          className="animate-fade-in absolute inset-0 bg-ink/25"
          onClick={onClose}
          aria-hidden="true"
        />

        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-label={`${label} detail`}
          className="animate-drawer-in absolute top-0 right-0 flex h-full w-[min(30rem,100vw)] flex-col border-l border-border bg-surface shadow-2xl shadow-black/10"
        >
          <div className="flex items-start justify-between gap-4 border-b border-border px-6 py-5">
            <div>
              <p className="text-xs text-ink-3">Dimension</p>
              <h2 className="mt-0.5 text-lg font-bold tracking-tight">{label}</h2>
            </div>
            <button
              ref={closeRef}
              type="button"
              onClick={onClose}
              aria-label="Close dimension detail"
              className="-mt-1 rounded-[var(--radius-control)] px-2 py-1 text-xl leading-none text-ink-3 transition hover:bg-ink/5 hover:text-ink"
            >
              &times;
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
            {isPending && <p className="text-sm text-ink-3">Loading rule detail&hellip;</p>}

            {error && (
              <p className="text-sm text-critical">
                {error?.message ?? 'Could not load this dimension.'}
              </p>
            )}

            {data && data.score === null && (
              <>
                <span
                  className={`inline-block rounded-full px-2.5 py-1 text-[11px] font-semibold ${bandPill(
                    data.score,
                  )}`}
                >
                  {bandLabel(data.score)}
                </span>
                <p className="mt-4 text-sm leading-relaxed text-ink-2">
                  {data.notAssessedReason ??
                    'There was not enough information in this file to assess this dimension.'}
                </p>
              </>
            )}

            {data && data.score !== null && (
              <>
                <div className="flex items-center gap-4">
                  <p className={`font-mono text-4xl font-semibold ${bandText(data.score)}`}>
                    {formatScore(data.score)}
                  </p>
                  <span
                    className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${bandPill(
                      data.score,
                    )}`}
                  >
                    {bandLabel(data.score)}
                  </span>
                </div>

                <h3 className="mt-8 text-xs font-semibold tracking-[0.08em] text-ink-3 uppercase">
                  Rules applied
                </h3>
                <p className="mt-1 text-xs text-ink-3">
                  Select a rule to see its failing records.
                </p>

                <ul className="mt-3 space-y-1">
                  {data.rules.map((rule) => (
                    <li key={rule.id}>
                      <button
                        type="button"
                        onClick={() => setOpenRule(rule)}
                        className="flex w-full items-center justify-between gap-3 rounded-[var(--radius-control)] px-3 py-2.5 text-left transition hover:bg-ink/4"
                      >
                        <span className="min-w-0">
                          <span className="block truncate text-sm text-ink">{rule.name}</span>
                          <span className="mt-0.5 block font-mono text-[11px] text-ink-3">
                            {rule.id}
                            {rule.column && <span className="font-sans"> &middot; {rule.column}</span>}
                          </span>
                        </span>
                        <span className="flex shrink-0 items-center gap-2">
                          {rule.severity && (
                            <span
                              className={`text-[9px] font-bold tracking-wide uppercase ${
                                SEVERITY_TEXT[rule.severity] ?? 'text-ink-3'
                              }`}
                              title={`${rule.severity} severity`}
                            >
                              {rule.severity}
                            </span>
                          )}
                          <span
                            className={`font-mono text-sm ${bandText(rule.passRate * 100)}`}
                          >
                            {formatPercent(rule.passRate)}
                          </span>
                          <span aria-hidden="true" className="text-ink-3">
                            &rsaquo;
                          </span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </div>
      </div>

      {openRule && (
        <RuleExamplesOverlay
          runId={runId}
          dimensionKey={dimensionKey}
          rule={openRule}
          onClose={() => setOpenRule(null)}
        />
      )}
    </>
  );
}
