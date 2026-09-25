import { formatPercent } from '@/lib/format';

/**
 * The per-column table shared by ColumnProfilePanel (an optional re-review
 * after a run has completed) and AwaitingCdesView (the required first
 * confirmation out of awaiting_cdes). Purely presentational -- both callers
 * own the fetch and the selection state themselves, and differ only in their
 * outer chrome and the wording of their confirm action.
 */
export function ColumnChecklist({ columns, selected, onToggle }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-border text-left text-xs text-ink-3">
          <th scope="col" className="pb-2 pr-6 font-medium">
            CDE
          </th>
          <th scope="col" className="pb-2 pr-6 font-medium">
            Column
          </th>
          <th scope="col" className="pb-2 pr-6 font-medium">
            Type
          </th>
          <th scope="col" className="pb-2 pr-6 font-medium">
            Fill rate
          </th>
          <th scope="col" className="pb-2 pr-6 font-medium">
            Distinct
          </th>
          <th scope="col" className="pb-2 font-medium">
            Why
          </th>
        </tr>
      </thead>
      <tbody>
        {columns.map((column) => {
          const isSelected = selected.has(column.name);
          return (
            <tr key={column.name} className="border-b border-border/70 align-top">
              <td className="py-3 pr-6">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => onToggle(column.name)}
                    aria-label={`Use ${column.name} as a critical data element`}
                    className="size-3.5 accent-accent"
                  />
                  {isSelected !== column.isCde && (
                    <span
                      className="text-[10px] text-ink-3"
                      title="Different from what the engine detected"
                    >
                      (changed)
                    </span>
                  )}
                </label>
              </td>
              <td className="py-3 pr-6 font-mono text-ink">{column.name}</td>
              <td className="py-3 pr-6 text-ink-2">{column.inferredType}</td>
              <td className="py-3 pr-6 font-mono text-ink-2">{formatPercent(column.fillRate)}</td>
              <td className="py-3 pr-6 font-mono text-ink-2">
                {formatPercent(column.distinctRatio)}
              </td>
              <td className="py-3 text-ink-2">{column.cdeReason}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
