import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DimensionDrawer } from './DimensionDrawer';
import * as api from '@/api/runs';

vi.mock('@/api/runs', async (importOriginal) => ({
  ...(await importOriginal()),
  getDimension: vi.fn(),
  getRuleExamples: vi.fn(),
}));

// Mirrors the shape integrity actually returns: two whole-record rules with
// column: null, two per-column rules with a real column. API_CONTRACT.md
// revision 3 explicitly flags this as worth re-checking, since integrity is
// the first dimension where the majority of rules are record-scoped.
const INTEGRITY_DETAIL = {
  key: 'integrity',
  score: 82,
  rules: [
    { id: 'INT-CARDINALITY', name: 'Cross-field cardinality holds', passRate: 0.9, column: null, severity: 'high' },
    { id: 'INT-DEPENDENT-FIELD', name: 'Dependent field completeness', passRate: 0.88, column: null, severity: 'high' },
    {
      id: 'INT-POSTCODE-COUNTRY-01',
      name: 'Postcode matches country',
      passRate: 0.95,
      column: 'postal_code',
      severity: 'medium',
    },
  ],
};

describe('DimensionDrawer rendering a rule with column: null', () => {
  it('never prints the literal string "null" for a whole-record rule', async () => {
    api.getDimension.mockResolvedValue(INTEGRITY_DETAIL);
    render(<DimensionDrawer runId="run_1" dimensionKey="integrity" onClose={vi.fn()} />);

    expect(await screen.findByText('Cross-field cardinality holds')).toBeInTheDocument();

    // The whole-record rules must not render "INT-CARDINALITY · null" or any
    // bare "null" text anywhere in the drawer.
    expect(screen.queryByText(/null/i)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\bnull\b/i);
  });

  it('still shows the column for a per-column rule, with the separator', async () => {
    api.getDimension.mockResolvedValue(INTEGRITY_DETAIL);
    render(<DimensionDrawer runId="run_1" dimensionKey="integrity" onClose={vi.fn()} />);

    await screen.findByText('Postcode matches country');
    expect(screen.getByText((_, node) => node?.textContent === 'INT-POSTCODE-COUNTRY-01 \u00b7 postal_code')).toBeInTheDocument();
  });

  it('the whole-record rule id renders with no trailing separator or column text', async () => {
    api.getDimension.mockResolvedValue(INTEGRITY_DETAIL);
    render(<DimensionDrawer runId="run_1" dimensionKey="integrity" onClose={vi.fn()} />);

    await screen.findByText('Cross-field cardinality holds');
    expect(screen.getByText('INT-CARDINALITY')).toBeInTheDocument();
  });
});
