import { useContext, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { DIMENSIONS, STATUS } from '@/api/constants';
import { getRun } from '@/api/runs';
import { getAssessment } from '@/api/salesforce';
import { RunsContext } from '@/components/RunsProvider';
import { bandBar, bandText } from '@/lib/band';
import { formatScore } from '@/lib/format';

// A results overview across every scored run and Salesforce assessment: a
// bar per item for its overall score and a column per dimension for the
// average, all on the same 0-100 scale as the tiles. Built from plain
// elements rather than a chart library, with the 70 and 90 band thresholds
// drawn in so a bar's colour and position always agree with lib/band.js.

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'files', label: 'Files' },
  { key: 'salesforce', label: 'Salesforce' },
];

const SORTS = [
  { key: 'lowest', label: 'Lowest first' },
  { key: 'highest', label: 'Highest first' },
  { key: 'name', label: 'Name' },
];

const TICKS = [0, 25, 50, 75, 100];
const THRESHOLDS = [70, 90];

function StatCard({ label, value, tone = 'text-ink', hint }) {
  return (
    <div className="rounded-[var(--radius-card)] border border-border bg-surface px-5 py-4">
      <p className="text-xs text-ink-3">{label}</p>
      <p className={`mt-1 font-mono text-2xl font-semibold ${tone}`}>{value}</p>
      {hint && <p className="mt-0.5 text-[11px] text-ink-3">{hint}</p>}
    </div>
  );
}

function Segmented({ options, value, onChange, label }) {
  return (
    <div role="group" aria-label={label} className="flex gap-1">
      {options.map((option) => (
        <button
          key={option.key}
          type="button"
          aria-pressed={value === option.key}
          onClick={() => onChange(option.key)}
          className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
            value === option.key ? 'bg-accent text-white' : 'bg-ink/5 text-ink-2 hover:bg-ink/8'
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

// The dashed band thresholds and tick gridlines behind a horizontal chart.
function Gridlines() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0">
      {TICKS.map((tick) => (
        <span
          key={tick}
          className="absolute inset-y-0 border-l border-border/70"
          style={{ left: `${tick}%` }}
        />
      ))}
      {THRESHOLDS.map((threshold) => (
        <span
          key={threshold}
          className="absolute inset-y-0 border-l border-dashed border-ink-3/50"
          style={{ left: `${threshold}%` }}
        />
      ))}
    </div>
  );
}

function ScoreBars({ items, onOpen }) {
  return (
    <div>
      <ul className="space-y-2.5">
        {items.map((item) => (
          <li key={item.key} className="grid grid-cols-[11rem_1fr_3rem] items-center gap-3">
            <button
              type="button"
              onClick={() => onOpen(item)}
              className="min-w-0 text-left"
              title={item.label}
            >
              <span className="block truncate text-sm text-ink hover:text-accent">
                {item.label}
              </span>
              <span className="block text-[11px] text-ink-3">{item.kindLabel}</span>
            </button>
            <button
              type="button"
              onClick={() => onOpen(item)}
              aria-label={`${item.label}: ${formatScore(item.overall)} out of 100`}
              className="relative h-6 rounded bg-ink/4"
            >
              <Gridlines />
              <span
                className={`absolute inset-y-1 left-0 rounded-r ${bandBar(item.overall)} transition-[width] duration-500`}
                style={{ width: `${Math.max(0, Math.min(100, item.overall))}%` }}
              />
            </button>
            <span
              className={`text-right font-mono text-sm font-semibold ${bandText(item.overall)}`}
            >
              {formatScore(item.overall)}
            </span>
          </li>
        ))}
      </ul>
      {/* Axis, aligned under the bar column only. */}
      <div className="mt-2 grid grid-cols-[11rem_1fr_3rem] gap-3">
        <span />
        <div className="relative h-4 font-mono text-[10px] text-ink-3">
          {TICKS.map((tick) => (
            <span key={tick} className="absolute -translate-x-1/2" style={{ left: `${tick}%` }}>
              {tick}
            </span>
          ))}
        </div>
        <span />
      </div>
    </div>
  );
}

function DimensionColumns({ averages }) {
  return (
    <div className="relative">
      {/* Horizontal gridlines at the ticks, thresholds dashed. */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 h-48">
        {TICKS.map((tick) => (
          <span
            key={tick}
            className="absolute inset-x-0 flex items-center border-t border-border/70"
            style={{ bottom: `${tick}%` }}
          >
            <span className="-mt-px -translate-y-1/2 pr-1 font-mono text-[10px] text-ink-3">
              {tick}
            </span>
          </span>
        ))}
        {THRESHOLDS.map((threshold) => (
          <span
            key={threshold}
            className="absolute inset-x-0 border-t border-dashed border-ink-3/50"
            style={{ bottom: `${threshold}%` }}
          />
        ))}
      </div>

      <ul className="relative grid h-48 grid-cols-7 gap-3 pl-8">
        {DIMENSIONS.map((dimension) => {
          const { average, count } = averages[dimension.key];
          const height = average === null ? 0 : Math.max(0, Math.min(100, average));
          return (
            <li key={dimension.key} className="flex h-full flex-col justify-end">
              <span
                className={`mb-1 text-center font-mono text-xs font-semibold ${bandText(average)}`}
              >
                {average === null ? 'n/a' : formatScore(average)}
              </span>
              <span
                role="img"
                aria-label={`${dimension.label}: ${
                  average === null ? 'not assessed' : `${formatScore(average)} out of 100`
                }, from ${count} scored`}
                className={`mx-auto w-full max-w-12 rounded-t ${bandBar(average)} transition-[height] duration-500`}
                style={{ height: `${height}%` }}
              />
            </li>
          );
        })}
      </ul>
      <ul className="mt-2 grid grid-cols-7 gap-3 pl-8">
        {DIMENSIONS.map((dimension) => (
          <li key={dimension.key} className="truncate text-center text-[11px] text-ink-2">
            {dimension.label}
          </li>
        ))}
      </ul>
    </div>
  );
}

function average(values) {
  const scored = values.filter((v) => typeof v === 'number');
  if (scored.length === 0) return { average: null, count: 0 };
  return {
    average: Math.round((scored.reduce((a, b) => a + b, 0) / scored.length) * 10) / 10,
    count: scored.length,
  };
}

export function DashboardPanel() {
  const { runs, assessments } = useContext(RunsContext);
  const navigate = useNavigate();
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState('lowest');

  // Object runs belong to their assessment's result; counting them as files
  // too would weigh the same records twice.
  const fileRuns = (runs ?? []).filter((run) => !run.assessmentId);
  const scoredItems = [
    ...fileRuns
      .filter((run) => run.status === STATUS.COMPLETED)
      .map((run) => ({
        key: `run:${run.id}`,
        id: run.id,
        kind: 'files',
        kindLabel: 'File',
        label: run.file,
        overall: run.overall,
      })),
    ...(assessments ?? [])
      .filter((a) => a.status === STATUS.COMPLETED)
      .map((a) => ({
        key: `assessment:${a.id}`,
        id: a.id,
        kind: 'salesforce',
        kindLabel: `Salesforce · ${a.objects} objects`,
        label: a.name,
        overall: a.overall,
      })),
  ];

  // Dimension scores aren't in the list endpoints, so each scored item's
  // detail is fetched once. The key is every scored item with its overall, so
  // a new result or a re-score refetches and nothing else does.
  const detailKey = scoredItems.map((item) => `${item.key}@${item.overall}`).join('|');
  const [scoresByKey, setScoresByKey] = useState({});

  useEffect(() => {
    let cancelled = false;
    const targets = detailKey ? detailKey.split('|').map((entry) => entry.split('@')[0]) : [];
    Promise.allSettled(
      targets.map((key) => {
        const [kind, id] = key.split(/:(.+)/);
        return (kind === 'run' ? getRun(id) : getAssessment(id)).then((detail) => [
          key,
          detail.scores ?? {},
        ]);
      }),
    ).then((results) => {
      if (cancelled) return;
      const next = {};
      results.forEach((result) => {
        if (result.status === 'fulfilled') next[result.value[0]] = result.value[1];
      });
      setScoresByKey(next);
    });
    return () => {
      cancelled = true;
    };
  }, [detailKey]);

  const visible = scoredItems.filter((item) => filter === 'all' || item.kind === filter);
  const sorted = [...visible].sort((a, b) => {
    if (sort === 'name') return a.label.localeCompare(b.label);
    return sort === 'lowest' ? a.overall - b.overall : b.overall - a.overall;
  });

  const averages = Object.fromEntries(
    DIMENSIONS.map((dimension) => [
      dimension.key,
      average(visible.map((item) => scoresByKey[item.key]?.[dimension.key])),
    ]),
  );
  const overallAverage = average(visible.map((item) => item.overall)).average;
  const criticalCount = visible.filter((item) => item.overall < 70).length;
  const reviewCount = [
    ...(filter !== 'salesforce' ? fileRuns : []),
    ...(filter !== 'files' ? (assessments ?? []) : []),
  ].filter((item) => item.status === STATUS.AWAITING_CDES).length;
  const detailsLoaded = visible.every((item) => scoresByKey[item.key]);

  function open(item) {
    navigate(
      item.kind === 'files'
        ? `/?run=${encodeURIComponent(item.id)}`
        : `/?assessment=${encodeURIComponent(item.id)}`,
    );
  }

  const isLoading = runs === null && assessments === null;

  return (
    <div className="px-10 py-9">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-sm text-ink-2">
            Every scored result on one scale, out of 100. Dashed lines mark the 70 and 90 band
            thresholds.
          </p>
        </div>
        <Segmented options={FILTERS} value={filter} onChange={setFilter} label="Show" />
      </div>

      <div className="mt-6 grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-4">
        <StatCard label="Scored results" value={visible.length} />
        <StatCard
          label="Average overall"
          value={formatScore(overallAverage)}
          tone={bandText(overallAverage)}
          hint="Each result counts once"
        />
        <StatCard
          label="Below 70"
          value={criticalCount}
          tone={criticalCount > 0 ? 'text-critical' : 'text-ink'}
          hint="Critical band"
        />
        <StatCard
          label="Waiting on review"
          value={reviewCount}
          tone={reviewCount > 0 ? 'text-accent' : 'text-ink'}
          hint="CDEs to confirm"
        />
      </div>

      {isLoading && <p className="mt-10 text-sm text-ink-3">Loading results&hellip;</p>}

      {!isLoading && visible.length === 0 && (
        <p className="mt-10 rounded-[var(--radius-card)] border border-border bg-surface px-5 py-8 text-center text-sm text-ink-3">
          Nothing scored yet{filter !== 'all' && ' in this view'}. Results appear here once an
          assessment completes.
        </p>
      )}

      {visible.length > 0 && (
        <>
          <section className="mt-8 rounded-[var(--radius-card)] border border-border bg-surface p-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-ink">Average by dimension</h2>
                <p className="mt-0.5 text-xs text-ink-3">
                  Mean of each dimension across the results shown. Not-assessed dimensions are left
                  out, not counted as zero.
                </p>
              </div>
              {!detailsLoaded && <p className="text-xs text-ink-3">Loading scores&hellip;</p>}
            </div>
            <div className="mt-6">
              <DimensionColumns averages={averages} />
            </div>
          </section>

          <section className="mt-6 rounded-[var(--radius-card)] border border-border bg-surface p-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-ink">Overall score by result</h2>
                <p className="mt-0.5 text-xs text-ink-3">Click a bar to open its report.</p>
              </div>
              <Segmented options={SORTS} value={sort} onChange={setSort} label="Sort" />
            </div>
            <div className="mt-6">
              <ScoreBars items={sorted} onOpen={open} />
            </div>
          </section>
        </>
      )}
    </div>
  );
}
