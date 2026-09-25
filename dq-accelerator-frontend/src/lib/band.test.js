import { describe, expect, it } from 'vitest';
import { BAND, bandLabel, scoreBand } from './band';

describe('scoreBand', () => {
  it('treats 90 and above as healthy (FR-4.5)', () => {
    expect(scoreBand(90)).toBe(BAND.HEALTHY);
    expect(scoreBand(100)).toBe(BAND.HEALTHY);
  });

  it('treats 70 through 89.9 as needs attention', () => {
    expect(scoreBand(89.9)).toBe(BAND.NEEDS_ATTENTION);
    expect(scoreBand(70)).toBe(BAND.NEEDS_ATTENTION);
  });

  it('treats below 70 as critical', () => {
    expect(scoreBand(69.9)).toBe(BAND.CRITICAL);
    expect(scoreBand(0)).toBe(BAND.CRITICAL);
  });

  it('labels each band the way the drawer expects', () => {
    expect(bandLabel(95)).toBe('Healthy');
    expect(bandLabel(75)).toBe('Needs attention');
    expect(bandLabel(40)).toBe('Critical');
  });
});

describe('scoreBand with a not-assessed dimension', () => {
  it('treats null and undefined as their own band, not critical', () => {
    expect(scoreBand(null)).toBe(BAND.NOT_ASSESSED);
    expect(scoreBand(undefined)).toBe(BAND.NOT_ASSESSED);
  });

  it('labels a not-assessed score distinctly from a bad score', () => {
    expect(bandLabel(null)).toBe('Not assessed');
    expect(bandLabel(0)).toBe('Critical');
  });
});
