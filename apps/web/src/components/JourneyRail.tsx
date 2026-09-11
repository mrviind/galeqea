import clsx from 'clsx';

/**
 * The Golden Path progress rail: Target → … → Keep green.
 *
 * Drawn wherever the journey is shown (the chat header, and each stage card), so
 * the whole arc is always visible and a reader can see at a glance how far a
 * target has come. Done stages are filled, the current one is ringed, future
 * ones are muted.
 */

export interface RailStage {
  stage: string;
  done: boolean;
  current: boolean;
  skipped?: boolean;
}

const LABELS: Record<string, string> = {
  target: 'Target', access: 'Access', guardrails: 'Guardrails', explore: 'Explore',
  plan: 'Plan', build: 'Build', smoke: 'Smoke', run: 'Run', triage: 'Triage',
  exploratory: 'Explore+', readiness: 'Readiness', report: 'Report', keep_green: 'Keep green',
};

export function stageLabel(stage: string): string {
  return LABELS[stage] ?? stage;
}

export function JourneyRail({ rail, compact = false }: { rail: RailStage[]; compact?: boolean }) {
  const currentIdx = rail.findIndex((s) => s.current);
  const state = (s: RailStage) => (s.current ? 'current' : s.done ? 'done' : s.skipped ? 'skipped' : 'future');
  // In compact mode, spell out only the current stage and the one that comes next
  // ("Plan → Build"); the rest are dots. Full mode labels every stage.
  // In compact mode, spell out the current stage, the one just before it and the
  // one that comes next; a skipped stage is always spelled out (an anomaly is
  // worth a word, not a dot). Everything else becomes a small step indicator,
  // filled for done, hollow for future: a real stepper, not a punctuation mark.
  const labelled = (i: number) => !compact || i === currentIdx || i === currentIdx + 1 || i === currentIdx - 1;

  return (
    <div className="flex items-center gap-0.5 overflow-x-auto" role="list" aria-label="Journey progress">
      {rail.map((s, i) => (
        <div key={s.stage} className="flex shrink-0 items-center">
          {labelled(i) || s.skipped ? (
            <span
              role="listitem"
              title={`${stageLabel(s.stage)}: ${state(s)}`}
              aria-label={`${stageLabel(s.stage)}: ${state(s)}`}
              className={clsx(
                'whitespace-nowrap rounded-sm px-1.5 py-0.5 text-[10px] font-medium transition',
                s.current && 'bg-accent/15 text-accent ring-1 ring-accent/40',
                s.done && 'text-pass',
                s.skipped && 'text-ink-3 line-through decoration-ink-3/50',
                !s.current && !s.done && !s.skipped && 'text-ink-3',
              )}
            >
              {stageLabel(s.stage)}
            </span>
          ) : (
            <span
              role="listitem"
              title={`${stageLabel(s.stage)}: ${state(s)}`}
              aria-label={`${stageLabel(s.stage)}: ${state(s)}`}
              className={clsx(
                'h-1.5 w-1.5 shrink-0 rounded-full transition',
                s.done ? 'bg-pass' : 'border border-line-strong',
              )}
            />
          )}
          {i < rail.length - 1 && (
            <span className={clsx('mx-0.5 h-px w-2 shrink-0', s.done ? 'bg-pass/50' : 'bg-line')} />
          )}
        </div>
      ))}
    </div>
  );
}
