import { useEffect, useMemo, useRef, useState } from 'react';
import clsx from 'clsx';
import { ArrowDownToLine, Pause, Play, Terminal } from 'lucide-react';
import { clock } from '../lib/format';
import { useApp } from '../state';
import type { StreamEvent } from '../lib/stream';

const LOG_TYPES = [
  'run.log', 'run.step', 'run.test.started', 'run.test.finished',
  'run.started', 'run.finished', 'run.progress', 'run.handoff',
  'heal.proposed', 'anomaly.detected', 'agent.error',
];

/**
 * The live execution log.
 *
 * Auto-follow releases the moment the user scrolls up: reading a failure while
 * the view yanks itself to the bottom every 40ms is the fastest way to make a
 * live log useless.
 */
export function LiveLog({ runId, className }: { runId?: string; className?: string }) {
  const { events } = useApp();
  const [follow, setFollow] = useState(true);
  const scroller = useRef<HTMLDivElement>(null);

  const lines = useMemo(
    () => events
      .filter((e) => LOG_TYPES.includes(e.type))
      .filter((e) => !runId || e.run_id === runId)
      .slice(-400),
    [events, runId],
  );

  useEffect(() => {
    if (!follow) return;
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [lines, follow]);

  return (
    <div className={clsx('flex min-h-0 flex-col', className)}>
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-line px-3">
        <Terminal size={13} className="text-ink-3" />
        <span className="text-[12px] font-medium">Execution log</span>
        <span className="mono text-[10.5px] text-ink-3">{lines.length}</span>
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => setFollow((v) => !v)}
            title={follow ? 'Pause auto-follow' : 'Resume auto-follow'}
            className={clsx(
              'flex items-center gap-1 px-1.5 py-1 text-[10.5px] transition-colors',
              follow ? 'text-accent hover:bg-surface-3' : 'text-ink-3 hover:bg-surface-3 hover:text-ink-2',
            )}
          >
            {follow ? <Pause size={10} /> : <Play size={10} />}
            {follow ? 'Following' : 'Paused'}
          </button>
          <button
            onClick={() => {
              setFollow(true);
              scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' });
            }}
            className="rounded-lg p-1 text-ink-3 transition hover:bg-surface-3 hover:text-ink"
            title="Jump to latest"
          >
            <ArrowDownToLine size={11} />
          </button>
        </div>
      </div>

      <div
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          if (!atBottom && follow) setFollow(false);
        }}
        className="min-h-0 flex-1 overflow-y-auto px-3 py-2"
      >
        {lines.length === 0 && (
          <p className="py-6 text-center text-[11.5px] text-ink-3">
            Nothing running. Start a run and every step appears here as it happens.
          </p>
        )}
        {lines.map((event) => <LogLine key={event.id} event={event} />)}
      </div>
    </div>
  );
}

function LogLine({ event }: { event: StreamEvent }) {
  const { text, tone } = describe(event);
  const tones: Record<string, string> = {
    ok: 'text-pass', fail: 'text-fail', warn: 'text-flaky',
    info: 'text-ink-2', dim: 'text-ink-3', brand: 'text-accent',
  };
  return (
    <div className="mono flex items-baseline gap-2 py-[1.5px] leading-relaxed">
      <span className="shrink-0 text-[10px] text-ink-3">{clock(event.ts)}</span>
      <span className={clsx('min-w-0 break-words', tones[tone] ?? tones.dim)}>{text}</span>
    </div>
  );
}

function describe(event: StreamEvent): { text: string; tone: string } {
  const p = event.payload ?? {};
  switch (event.type) {
    case 'run.started':
      return { text: `▶ run started · ${p.test_count} test(s) · ${(p.browsers ?? []).join(', ')} · ${p.base_url ?? ''}`, tone: 'brand' };
    case 'run.test.started':
      return { text: `  ┌ ${p.key ?? ''} ${p.title ?? ''} [${p.browser ?? ''}]`, tone: 'info' };
    case 'run.step': {
      if (p.phase === 'start') return { text: ` │ ${String(p.index).padStart(2, '0')} ${p.action} — ${p.intent ?? ''}`, tone: 'dim' };
      const mark = p.status === 'passed' ? '✓' : p.status === 'failed' ? '✗' : '·';
      const heal = p.healed ? ' ⟲ healed' : '';
      const err = p.error ? `  ${p.error}` : '';
      return {
        text: `  │ ${String(p.index).padStart(2, '0')} ${mark} ${p.action} ${p.duration_ms ?? 0}ms${heal}${err}`,
        tone: p.status === 'failed' ? 'fail' : p.healed ? 'warn' : 'ok',
      };
    }
    case 'run.test.finished':
      return {
        text: `  └ ${p.key ?? ''} ${p.status} ${p.duration_ms ?? 0}ms${p.error ? ` — ${p.error}` : ''}`,
        tone: p.status === 'passed' ? 'ok' : 'fail',
      };
    case 'run.progress':
      return p.completed !== undefined
        ? { text: `  ⋯ ${p.completed}/${p.total} complete`, tone: 'dim' }
        : { text: `  ⋯ ${p.phase ?? ''}`, tone: 'dim' };
    case 'run.finished':
      return { text: `■ run ${p.status} · ${JSON.stringify(p.totals ?? {})}`, tone: p.status === 'passed' ? 'ok' : 'fail' };
    case 'run.handoff':
      return { text: `⏸ waiting for a human — ${p.reason ?? ''} (${p.url ?? ''})`, tone: 'warn' };
    case 'heal.proposed':
      return {
        text: p.kind === 'proposal'
          ? `⟲ heal ${p.ok ? 'proposed' : 'declined'} via ${p.strategy} (${(p.score ?? 0).toFixed(2)}) — ${p.reason ?? ''}`
          : `⟲ ${p.kind}: ${p.from ?? ''} → ${p.to ?? ''}`,
        tone: 'warn',
      };
    case 'anomaly.detected':
      return { text: `⚠ anomaly: ${p.label} ${p.metric} ${p.observed} vs ${p.expected} (${p.sigma}σ)`, tone: 'warn' };
    case 'agent.error':
      return { text: `✗ ${p.message ?? ''}`, tone: 'fail' };
    case 'run.log':
      return { text: `  ${p.message ?? ''}`, tone: p.level === 'stderr' ? 'warn' : 'dim' };
    default:
      return { text: `${event.type} ${JSON.stringify(p).slice(0, 200)}`, tone: 'dim' };
  }
}
