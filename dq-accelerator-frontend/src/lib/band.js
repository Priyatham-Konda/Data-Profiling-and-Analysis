// FR-4.5 score bands. Single source of truth for the >=90 / 70-89 / <70
// thresholds -- no component re-derives these.
//
// A dimension the engine couldn't evaluate (e.g. timeliness on a file with no
// date column) reports score: null, not 0 -- scoring it 0 would read as
// "critical" when the honest answer is "we don't know". NOT_ASSESSED is a
// fourth band, deliberately outside the healthy/warn/critical spectrum, so a
// null can never silently fall through to "critical" by accident.

export const BAND = Object.freeze({
  HEALTHY: 'healthy',
  NEEDS_ATTENTION: 'needs-attention',
  CRITICAL: 'critical',
  NOT_ASSESSED: 'not-assessed',
});

export function scoreBand(score) {
  if (score === null || score === undefined) return BAND.NOT_ASSESSED;
  if (score >= 90) return BAND.HEALTHY;
  if (score >= 70) return BAND.NEEDS_ATTENTION;
  return BAND.CRITICAL;
}

const LABELS = {
  [BAND.HEALTHY]: 'Healthy',
  [BAND.NEEDS_ATTENTION]: 'Needs attention',
  [BAND.CRITICAL]: 'Critical',
  [BAND.NOT_ASSESSED]: 'Not assessed',
};

export function bandLabel(score) {
  return LABELS[scoreBand(score)];
}

// Tailwind classes per band. Kept here so colour and threshold never disagree.
const TEXT = {
  [BAND.HEALTHY]: 'text-healthy',
  [BAND.NEEDS_ATTENTION]: 'text-warn',
  [BAND.CRITICAL]: 'text-critical',
  [BAND.NOT_ASSESSED]: 'text-ink-3',
};

const PILL = {
  [BAND.HEALTHY]: 'bg-healthy/10 text-healthy',
  [BAND.NEEDS_ATTENTION]: 'bg-warn/10 text-warn',
  [BAND.CRITICAL]: 'bg-critical/10 text-critical',
  [BAND.NOT_ASSESSED]: 'bg-ink-3/10 text-ink-3',
};

const BAR = {
  [BAND.HEALTHY]: 'bg-healthy',
  [BAND.NEEDS_ATTENTION]: 'bg-warn',
  [BAND.CRITICAL]: 'bg-critical',
  [BAND.NOT_ASSESSED]: 'bg-border',
};

export const bandText = (score) => TEXT[scoreBand(score)];
export const bandPill = (score) => PILL[scoreBand(score)];
export const bandBar = (score) => BAR[scoreBand(score)];
