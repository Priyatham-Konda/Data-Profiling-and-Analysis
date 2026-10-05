export function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

export function formatCount(n) {
  return typeof n === 'number' ? n.toLocaleString('en-US') : '—';
}

export function formatScore(n) {
  return typeof n === 'number' ? n.toFixed(1) : '—';
}

export function formatPercent(fraction) {
  return `${Math.round(fraction * 100)}%`;
}
