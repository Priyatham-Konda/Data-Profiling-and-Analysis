import { formatPercent } from '@/lib/format';

// Revision 5: a Salesforce object's columns carry a `salesforce` block. Its
// label goes first (Total_Amount_Debt__c means little; "Total Amount of Debt"
// means something), with the API name beside it, plus badges for required
// fields and for reference fields' targets.
function ColumnName({ column }) {
  const meta = column.salesforce;
  if (!meta) return <span className="font-mono text-ink">{column.name}</span>;

  return (
    <div>
      <p className="text-ink">{meta.label}</p>
      <p className="mt-0.5 font-mono text-[11px] text-ink-3">{column.name}</p>
      {(meta.required || meta.referenceTo?.length > 0) && (
        <div className="mt-1 flex flex-wrap gap-1">
          {meta.required && (
            <span className="rounded-full bg-ink/6 px-1.5 py-0.5 text-[10px] font-semibold text-ink-2">
              Required
            </span>
          )}
          {meta.referenceTo?.length > 0 && (
            <span className="rounded-full bg-accent/10 px-1.5 py-0.5 text-[10px] font-semibold text-accent">
              &rarr; {meta.referenceTo.join(', ')}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The per-column table shared by ColumnProfilePanel (an optional re-review
 * after a run has completed), AwaitingCdesView (the required first
 * confirmation out of awaiting_cdes), and each tab of a Salesforce
 * assessment's confirm screen. Purely presentational -- callers own the fetch
 * and the selection state, and differ only in their chrome and confirm action.
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
              <td className="py-3 pr-6">
                <ColumnName column={column} />
              </td>
              <td className="py-3 pr-6 text-ink-2">
                {column.salesforce?.type ?? column.inferredType}
              </td>
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
