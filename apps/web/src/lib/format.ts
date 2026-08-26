import type { RunStatus } from './api';

// Status is the only place saturation is spent. Hues are GitHub Primer's
// dark-mode set, chosen because their contrast on a near-black ground is
// already proven rather than eyeballed.
export const STATUS_META: Record<string, { label: string; color: string; bg: string; dot: string }> = {
  passed:       { label: 'Passed',       color: 'text-pass',    bg: 'bg-pass/10 border-pass/30',       dot: 'bg-pass' },
  failed:       { label: 'Failed',       color: 'text-fail',    bg: 'bg-fail/10 border-fail/30',       dot: 'bg-fail' },
  error:        { label: 'Error',        color: 'text-fail',    bg: 'bg-fail/10 border-fail/30',       dot: 'bg-fail' },
  flaky:        { label: 'Flaky',        color: 'text-flaky',   bg: 'bg-flaky/10 border-flaky/30',     dot: 'bg-flaky' },
  blocked:      { label: 'Blocked',      color: 'text-blocked', bg: 'bg-blocked/10 border-blocked/30', dot: 'bg-blocked' },
  needs_review: { label: 'Needs review', color: 'text-review',  bg: 'bg-review/10 border-review/30',   dot: 'bg-review' },
  running:      { label: 'Running',      color: 'text-running', bg: 'bg-running/10 border-running/30', dot: 'bg-running' },
  queued:       { label: 'Queued',       color: 'text-ink-3',   bg: 'bg-surface-3 border-line',        dot: 'bg-ink-3' },
  skipped:      { label: 'Skipped',      color: 'text-ink-3',   bg: 'bg-surface-3 border-line',        dot: 'bg-ink-3' },
  cancelled:    { label: 'Cancelled',    color: 'text-ink-3',   bg: 'bg-surface-3 border-line',        dot: 'bg-ink-3' },
  unverified:   { label: 'Unverified',   color: 'text-flaky',   bg: 'bg-flaky/10 border-flaky/30',     dot: 'bg-flaky' },
};

export function statusMeta(status: string) {
  return STATUS_META[status] ?? STATUS_META.queued;
}

export function duration(ms: number): string {
  if (!ms) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m}m ${s}s`;
}

/**
 * Human-readable time offset, in either direction.
 *
 * Handles the future as well as the past: schedules show when they will *next*
 * fire, and treating that as elapsed time reported "just now" for something
 * eight hours away.
 */
export function relative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';

  const diff = Date.now() - then;
  const future = diff < 0;
  const seconds = Math.abs(Math.round(diff / 1000));

  const phrase = (value: number, unit: string) =>
    future ? `in ${value}${unit}` : `${value}${unit} ago`;

  if (seconds < 45) return future ? 'in a moment' : 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return phrase(minutes, 'm');
  const hours = Math.round(minutes / 60);
  if (hours < 24) return phrase(hours, 'h');
  const days = Math.round(hours / 24);
  if (days < 7) return phrase(days, 'd');

  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

export function pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export const RISK_COLOR: Record<string, string> = {
  critical: 'text-fail border-fail/30 bg-fail/10',
  high: 'text-flaky border-flaky/30 bg-flaky/10',
  medium: 'text-ink-2 border-line-strong bg-surface-3',
  low: 'text-ink-3 border-line bg-surface-3',
};
