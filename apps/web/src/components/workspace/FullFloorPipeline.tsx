import { Fragment } from 'react';
import type { LucideIcon } from 'lucide-react';
import { Compass, FileCheck2, ClipboardList, Zap } from 'lucide-react';

/**
 * "Here's what happens": the Full Floor's four phases, shown once so a new
 * user has a mental model before they type anything. Every line is a real,
 * shipped capability (self-healing, zero-token re-run, the no-self-approval
 * gate, the Word/Excel exports) rather than marketing copy.
 *
 * Deliberately not a row of four bordered cards, because that shape reads as a
 * generic SaaS feature grid the moment more than one product uses it. This is
 * an open, connected timeline instead: a node per phase sitting on a line
 * with a light continuously travelling through it (.flow-line, the same
 * "something is alive here" language as .agent-glow elsewhere in the app),
 * each phase rising into place with a short stagger rather than appearing
 * all at once.
 */
type Tone = 'review' | 'flaky' | 'accent' | 'pass';

type Phase = {
  label: string;
  icon: LucideIcon;
  tone: Tone;
  summary: string;
  items: string[];
};

const PHASES: Phase[] = [
  {
    label: 'Analyze', icon: Compass, tone: 'review',
    summary: 'A real browser explores the target. No crawler heuristics, no guesswork.',
    items: ['Every page, form and element mapped', 'Access & guardrails detected automatically', 'Nothing to configure to start'],
  },
  {
    label: 'Plan', icon: ClipboardList, tone: 'flaky',
    summary: 'A typed plan (functional, a11y, security and more) filed for review.',
    items: ['Requirement-derived cases included', 'A human approves every plan', 'Self-approval is structurally impossible'],
  },
  {
    label: 'Run', icon: Zap, tone: 'accent',
    summary: 'Every approved case executes for real, with the mechanics to make re-runs free.',
    items: ['Real browser execution (Playwright)', 'Self-healing locators', 'Zero-token re-run via the step cache'],
  },
  {
    label: 'Report', icon: FileCheck2, tone: 'pass',
    summary: 'A client-ready record of what ran, what failed, and why.',
    items: ['Screenshot evidence on every failure', 'Test Plan & Completion Report (Word/Excel)', 'Requirements traceability matrix'],
  },
];

const ICON_TEXT: Record<Tone, string> = {
  review: 'text-review', flaky: 'text-flaky', accent: 'text-accent', pass: 'text-pass',
};
const ICON_BG: Record<Tone, string> = {
  review: 'bg-review/15', flaky: 'bg-flaky/15', accent: 'bg-accent/15', pass: 'bg-pass/15',
};
const DOT_BG: Record<Tone, string> = {
  review: 'bg-review', flaky: 'bg-flaky', accent: 'bg-accent', pass: 'bg-pass',
};
const RULE_BORDER: Record<Tone, string> = {
  review: 'border-review/40', flaky: 'border-flaky/40', accent: 'border-accent/40', pass: 'border-pass/40',
};
const FLOW_DELAY = ['', 'flow-line-d1', 'flow-line-d2'];

export function FullFloorPipeline() {
  return (
    <div className="w-full max-w-4xl">
      <p className="mb-8 text-center text-[10.5px] font-semibold uppercase tracking-[0.15em] text-ink-3">
        What happens when you ask
      </p>
      <div className="flex flex-col gap-6 xl:flex-row xl:items-start xl:gap-0">
        {PHASES.map((phase, i) => (
          <Fragment key={phase.label}>
            <PhaseNode phase={phase} index={i} />
            {i < PHASES.length - 1 && (
              <div className="hidden shrink-0 items-center px-3 pt-5 xl:flex" style={{ width: 44 }}>
                <div className={`flow-line w-full ${FLOW_DELAY[i]}`} />
              </div>
            )}
          </Fragment>
        ))}
      </div>
    </div>
  );
}

function PhaseNode({ phase, index }: { phase: Phase; index: number }) {
  const Icon = phase.icon;
  return (
    <div
      className={`rise-in group flex flex-1 flex-col items-start border-l-2 pl-3 xl:border-l-0 xl:pl-0 ${RULE_BORDER[phase.tone]}`}
      style={{ animationDelay: `${index * 100}ms` }}
    >
      <div
        className={`flex h-10 w-10 items-center justify-center rounded-full transition-transform duration-300 group-hover:scale-110 ${ICON_BG[phase.tone]} ${ICON_TEXT[phase.tone]}`}
      >
        <Icon size={17} />
      </div>
      <h3 className="mt-3 text-[14.5px] font-bold tracking-tight text-ink">{phase.label}</h3>
      <p className="mt-1.5 text-[11.5px] leading-relaxed text-ink-3">{phase.summary}</p>
      <ul className="mt-3.5 space-y-2">
        {phase.items.map((item) => (
          <li key={item} className="flex items-start gap-2 text-[11px] leading-snug text-ink-2">
            <span className={`mt-[5px] h-1 w-1 shrink-0 rounded-full ${DOT_BG[phase.tone]}`} />
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}
