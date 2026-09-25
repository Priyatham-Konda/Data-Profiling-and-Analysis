import { DIMENSIONS } from '@/api/constants';
import { bandBar, bandLabel, bandPill, bandText } from '@/lib/band';
import { formatScore } from '@/lib/format';

// DimensionTile is only ever rendered by DimensionGrid, so it stays private
// to this file rather than having its own.

function DimensionTile({ label, score, reason, onOpen }) {
  // null/undefined score means the engine couldn't evaluate this dimension on
  // this file (e.g. timeliness with no date column) -- shown as a reason, not
  // as a fake 0 score with a critical-red bar.
  const notAssessed = score === null || score === undefined;

  // Mouse opens on double-click, per the spec. A double-click is not reachable
  // from a keyboard, so Enter/Space on the focused tile opens it too -- handled
  // via keydown rather than click, so a single mouse click still does nothing.
  function handleKeyDown(event) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onOpen();
    }
  }

  return (
    <button
      type="button"
      onDoubleClick={onOpen}
      onKeyDown={handleKeyDown}
      title="Double-click for rule detail"
      className="rounded-[var(--radius-card)] border border-border bg-surface p-5 text-left transition select-none hover:border-accent/40 hover:shadow-sm"
    >
      <div className="flex items-start justify-between gap-3">
        <span className="text-sm font-semibold text-ink">{label}</span>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${bandPill(score)}`}
        >
          {bandLabel(score)}
        </span>
      </div>

      {notAssessed ? (
        <p className="mt-3 text-xs leading-relaxed text-ink-3">
          {reason ?? 'Not enough information in this file to assess this dimension.'}
        </p>
      ) : (
        <>
          <p className={`mt-3 font-mono text-3xl font-semibold ${bandText(score)}`}>
            {formatScore(score)}
          </p>
          <div className="mt-3 h-1 w-full overflow-hidden rounded-full bg-border">
            <div
              className={`h-full rounded-full ${bandBar(score)}`}
              style={{ width: `${Math.min(100, Math.max(0, score))}%` }}
            />
          </div>
        </>
      )}
    </button>
  );
}

export function DimensionGrid({ scores, notAssessed = {}, onOpenDimension }) {
  return (
    <section className="mt-7">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-ink">Dimensions</h2>
        <p className="text-xs text-ink-3">Double-click a tile for rule detail</p>
      </div>

      <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
        {DIMENSIONS.map((dimension) => (
          <DimensionTile
            key={dimension.key}
            label={dimension.label}
            score={scores[dimension.key]}
            reason={notAssessed[dimension.key]}
            onOpen={() => onOpenDimension(dimension.key)}
          />
        ))}
      </div>
    </section>
  );
}
