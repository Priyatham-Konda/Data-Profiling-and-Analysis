import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DimensionGrid } from './DimensionGrid';

const SCORES = {
  completeness: 96,
  validity: 91,
  uniqueness: 84,
  consistency: 77,
  accuracy: 68,
  timeliness: null,
  integrity: 82,
};

describe('DimensionGrid with a not-assessed dimension', () => {
  it('shows the reason instead of a score, and a neutral pill instead of a band colour', () => {
    render(
      <DimensionGrid
        scores={SCORES}
        notAssessed={{ timeliness: 'No date column was detected in this file.' }}
        onOpenDimension={vi.fn()}
      />,
    );

    expect(screen.getByText(/no date column was detected/i)).toBeInTheDocument();
    expect(screen.getByText('Not assessed')).toBeInTheDocument();
    // The five real scores still render as numbers, unaffected -- two land in
    // the healthy band (completeness 96, validity 91).
    expect(screen.getByText('96.0')).toBeInTheDocument();
    expect(screen.getAllByText('Healthy')).toHaveLength(2);
  });

  it('does not crash and shows a generic reason when notAssessed has no entry for the key', () => {
    render(<DimensionGrid scores={SCORES} onOpenDimension={vi.fn()} />);
    expect(screen.getByText(/not enough information/i)).toBeInTheDocument();
  });
});
