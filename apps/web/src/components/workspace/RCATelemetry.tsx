import { useEffect, useRef } from 'react';
import clsx from 'clsx';
import { Activity, Loader2 } from 'lucide-react';
import { clock } from '../../lib/format';
import { useWorkspace } from '../../workspace';
import { EmptyPane } from './EmptyPane';

/** [RCA Telemetry] — a terminal-style view of the last execution. */
export function RCATelemetry() {
  const { activeTelemetry, activeReview, clearPane } = useWorkspace();
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' });
  }, [activeTelemetry.logs]);

  // A review is telemetry too: a verdict and a list of located findings. It
  // takes the pane when present, because a review the agent just produced is
  // what the user asked about; a stale run log underneath can be re-opened.
  if (activeReview) {
    return <ReviewView />;
  }

  if (activeTelemetry.status === 'idle') {
    return (
      <EmptyPane
        icon={<Activity size={11} />}
        title="No execution yet"
        body="Run a generated script from the Test Matrix and its output streams here, line by line, with the failing step and the evidence around it."
      />
    );
  }

  const { status, logs, target, durationMs } = activeTelemetry;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-line px-5 py-2.5">
        <Activity size={14} className="shrink-0 text-ink-3" />
        <h2 className="mono min-w-0 truncate text-[12px] text-ink">{target}</h2>

        <span className={clsx(
          'flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-0.5 text-[10.5px] font-medium',
          status === 'running' && 'border-accent/30 bg-accent/10 text-accent',
          status === 'passed' && 'border-pass/30 bg-pass/10 text-pass',
          status === 'failed' && 'border-fail/30 bg-fail/10 text-fail',
        )}>
          {status === 'running' && <Loader2 size={9} className="animate-spin" />}
          {status}
        </span>

        {durationMs != null && (
          <span className="shrink-0 text-[11px] text-ink-3 tabular-nums">
            {(durationMs / 1000).toFixed(1)}s
          </span>
        )}
        <button
          onClick={() => clearPane('telemetry')}
          className="ml-auto shrink-0 text-[11px] text-ink-3 transition hover:text-ink"
        >
          Clear
        </button>
      </header>

      {/* A terminal, not a table: monospace, gutter timestamps, and lines that
          keep their own width so a stack trace is not reflowed into nonsense. */}
      <div className="min-h-0 flex-1 overflow-auto bg-canvas px-4 py-3">
        <ol className="mono min-w-max space-y-0.5 text-[12px] leading-relaxed">
          {logs.map((line, index) => (
            <li key={index} className="flex gap-3">
              <span className="shrink-0 select-none text-ink-3/70 tabular-nums">{clock(line.at)}</span>
              <span className={clsx(
                'shrink-0 w-3 select-none text-center',
                line.level === 'pass' && 'text-pass',
                line.level === 'fail' && 'text-fail',
                line.level === 'warn' && 'text-flaky',
                line.level === 'info' && 'text-ink-3',
              )}>
                {line.level === 'pass' ? '✓' : line.level === 'fail' ? '✗' : line.level === 'warn' ? '!' : ' '}
              </span>
              <span className={clsx(
                'whitespace-pre',
                line.level === 'fail' ? 'text-fail' : line.level === 'warn' ? 'text-flaky' : 'text-ink-2',
              )}>
                {line.text}
              </span>
            </li>
          ))}
          {status === 'running' && (
            <li className="flex gap-3 text-ink-3">
              <span className="shrink-0 tabular-nums">{clock(new Date().toISOString())}</span>
              <span className="w-3" />
              <span className="animate-pulse">▍</span>
            </li>
          )}
        </ol>
        <div ref={bottom} />
      </div>
    </div>
  );
}


const VERDICT_META: Record<string, { label: string; tone: string }> = {
  sound: { label: 'Sound', tone: 'text-pass border-pass/30 bg-pass/10' },
  advisory: { label: 'Advisory', tone: 'text-ink-2 border-line bg-surface-2' },
  needs_work: { label: 'Needs work', tone: 'text-flaky border-flaky/30 bg-flaky/10' },
  blocked: { label: 'Blocked', tone: 'text-fail border-fail/30 bg-fail/10' },
};

const SEVERITY_TONE: Record<string, string> = {
  critical: 'text-fail', high: 'text-fail', medium: 'text-flaky', low: 'text-ink-3',
};

function ReviewView() {
  const { activeReview, clearPane } = useWorkspace();
  if (!activeReview) return null;
  const verdict = VERDICT_META[activeReview.verdict] ?? VERDICT_META.advisory;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-line px-5 py-2.5">
        <Activity size={14} className="shrink-0 text-ink-3" />
        <h2 className="min-w-0 truncate text-[13px] font-semibold text-ink">{activeReview.title}</h2>
        <span className={clsx('shrink-0 rounded-md border px-2 py-0.5 text-[10.5px] font-medium', verdict.tone)}>
          {verdict.label}
        </span>
        <button onClick={() => clearPane('rca')} className="ml-auto shrink-0 text-[11px] text-ink-3 transition hover:text-ink">Clear</button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {activeReview.findings.length === 0 ? (
          <p className="text-[12.5px] leading-relaxed text-ink-2">
            No structural problems found. The test asserts a traceable outcome with durable
            locators. A human should still confirm the assertion is the right one.
          </p>
        ) : (
          <ul className="space-y-2">
            {activeReview.findings.map((finding, index) => (
              <li key={index} className="flex gap-3 rounded-lg border border-line bg-surface-2/50 px-3 py-2">
                <span className={clsx('mono shrink-0 pt-0.5 text-[10px] font-semibold uppercase', SEVERITY_TONE[finding.severity])}>
                  {finding.severity}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-[12px] leading-relaxed text-ink-2">{finding.message}</p>
                  {finding.step != null && (
                    <p className="mono mt-0.5 text-[10.5px] text-ink-3">step {finding.step + 1} · {finding.kind}</p>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
