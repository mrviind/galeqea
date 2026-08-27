import { useEffect, useMemo, useState } from 'react';
import clsx from 'clsx';
import { Check, Copy, Grid3x3, ListChecks, Loader2, Play, TriangleAlert } from 'lucide-react';
import { highlight } from '../../lib/highlight';
import { relative } from '../../lib/format';
import { useWorkspace } from '../../workspace';
import { EmptyPane } from './EmptyPane';

/** Mock Playwright output. Written to look like the real reporter, and to fail. */
const MOCK_RUN: { level: 'info' | 'pass' | 'fail' | 'warn'; text: string; delay: number }[] = [
  { level: 'info', text: 'Running 1 test using 1 worker', delay: 120 },
  { level: 'pass', text: 'browser.newContext()', delay: 260 },
  { level: 'pass', text: "page.goto('/checkout')", delay: 340 },
  { level: 'pass', text: "getByLabel('Email address').fill('ravi@example.com')", delay: 300 },
  { level: 'warn', text: "locator resolved on fallback rung 2 — primary testid missing", delay: 220 },
  { level: 'fail', text: "locator('#checkout').click() — Timeout 30000ms exceeded", delay: 380 },
  { level: 'info', text: '  waiting for locator(\'#checkout\')', delay: 90 },
  { level: 'info', text: '  1 test failed, 0 passed (2.1s)', delay: 140 },
];

export function TestMatrix() {
  const { activeTestScript, activePlan, activeTelemetry, beginRun, appendLog, finishRun } = useWorkspace();

  // A plan takes the pane when present: it is what the agent just proposed and
  // wants confirmed, and it precedes any script it would generate.
  if (activePlan) {
    return <PlanView />;
  }

  const [selected, setSelected] = useState(0);
  const [copied, setCopied] = useState(false);

  const file = activeTestScript?.files[selected] ?? activeTestScript?.files[0];
  const code = useMemo(
    () => (file ? highlight(file.code, file.language || 'typescript') : ''),
    [file],
  );

  // A newly generated script must not leave the previous file's tab selected.
  useEffect(() => setSelected(0), [activeTestScript]);

  const running = activeTelemetry.status === 'running';

  const execute = async () => {
    if (!activeTestScript || running) return;
    // One clock for the whole run, started before the wait. Timing the loop
    // separately and adding the pre-roll back on afterwards gives the same
    // answer only when nothing else delays it — and browsers throttle timers in
    // a background tab, so the two diverge exactly when someone switches away.
    const started = Date.now();
    beginRun(activeTestScript.files[0]?.filename ?? activeTestScript.title);

    // Two seconds of apparent work before the reporter speaks, then lines one at
    // a time. A log that lands all at once reads as the canned string it is, and
    // the point of this mock is to prototype the shape of a real run.
    await new Promise((r) => setTimeout(r, 2000));
    for (const line of MOCK_RUN) {
      appendLog({ level: line.level, text: line.text });
      await new Promise((r) => setTimeout(r, line.delay));
    }
    finishRun('failed', Date.now() - started);
  };

  const copy = async () => {
    if (!file) return;
    try {
      await navigator.clipboard.writeText(file.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch { /* clipboard is unavailable outside a secure context */ }
  };

  if (!activeTestScript || !file) {
    return (
      <EmptyPane
        icon={<Grid3x3 size={11} />}
        title="No script generated"
        body="Give the agent a Gherkin scenario and the generated spec and page object appear here. Locators the scenario does not pin down arrive as TODOs — they are never guessed."
      />
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-line px-5 py-2.5">
        <Grid3x3 size={14} className="shrink-0 text-ink-3" />
        <h2 className="min-w-0 truncate text-[13px] font-semibold text-ink">{activeTestScript.title}</h2>
        {activeTestScript.requirementRef && (
          <span className="shrink-0 rounded-md border border-accent/30 bg-accent/10 px-1.5 py-px text-[10px] font-medium text-accent">
            {activeTestScript.requirementRef}
          </span>
        )}
        <span className="shrink-0 text-[11px] text-ink-3">{relative(activeTestScript.at)}</span>

        <button
          onClick={execute}
          disabled={running}
          className={clsx(
            'ml-auto flex shrink-0 items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] font-semibold transition',
            running
              ? 'border-line bg-surface-2 text-ink-3'
              : 'border-transparent bg-ink text-canvas hover:bg-white',
          )}
        >
          {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          {running ? 'Executing…' : 'Run Local Execution'}
        </button>
      </header>

      {!!activeTestScript.unresolved.length && (
        <div className="flex shrink-0 items-start gap-2 border-b border-line bg-flaky/[0.07] px-5 py-2">
          <TriangleAlert size={12} className="mt-0.5 shrink-0 text-flaky" />
          <p className="text-[11.5px] leading-relaxed text-flaky">
            {activeTestScript.unresolved.length} step(s) named no element, so no locator was
            derived. They are marked TODO and will throw until filled in against the real DOM.
          </p>
        </div>
      )}

      <div className="flex shrink-0 items-center gap-1 border-b border-line px-3">
        {activeTestScript.files.map((f, index) => (
          <button
            key={f.filename}
            onClick={() => setSelected(index)}
            className={clsx(
              'mono border-b-2 px-2.5 py-1.5 text-[11px] transition-colors',
              index === selected
                ? 'border-accent text-ink'
                : 'border-transparent text-ink-3 hover:text-ink-2',
            )}
          >
            {f.filename}
          </button>
        ))}
        <button
          onClick={copy}
          title={copied ? 'Copied' : 'Copy file'}
          className={clsx('ml-auto rounded-md p-1 transition', copied ? 'text-pass' : 'text-ink-3 hover:text-ink')}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto bg-canvas">
        <pre className="min-w-max px-5 py-4 text-[12.5px] leading-[1.65]">
          <code className="mono hljs" dangerouslySetInnerHTML={{ __html: code }} />
        </pre>
      </div>
    </div>
  );
}


const EFFECT_TONE: Record<string, string> = {
  'read-only': 'text-ink-3 border-line',
  writes: 'text-flaky border-flaky/30 bg-flaky/10',
  'needs approval': 'text-fail border-fail/30 bg-fail/10',
  unknown: 'text-fail border-fail/40 bg-fail/10',
};

function PlanView() {
  const { activePlan, clearPane } = useWorkspace();
  if (!activePlan) return null;
  const unknown = activePlan.steps.some((s) => !s.known_tool);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-line px-5 py-2.5">
        <ListChecks size={14} className="shrink-0 text-ink-3" />
        <h2 className="min-w-0 flex-1 truncate text-[13px] font-semibold text-ink">Plan</h2>
        {activePlan.writesState && (
          <span className="shrink-0 rounded-md border border-flaky/30 bg-flaky/10 px-2 py-0.5 text-[10px] font-medium text-flaky">
            changes state
          </span>
        )}
        <button onClick={() => clearPane('test_matrix')} className="shrink-0 text-[11px] text-ink-3 transition hover:text-ink">Clear</button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <p className="mb-4 text-[13px] leading-relaxed text-ink">{activePlan.goal}</p>

        <ol className="space-y-2">
          {activePlan.steps.map((step) => (
            <li key={step.index} className="flex gap-3 rounded-lg border border-line bg-surface-2/50 px-3 py-2.5">
              <span className="mono mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md border border-line bg-surface text-[10px] text-ink-3">
                {step.index}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="mono text-[12px] font-medium text-ink">{step.tool}</span>
                  <span className={clsx('shrink-0 rounded border px-1.5 py-px text-[9.5px]', EFFECT_TONE[step.effect] ?? EFFECT_TONE.unknown)}>
                    {step.effect}
                  </span>
                </div>
                <p className="mt-0.5 text-[11.5px] leading-relaxed text-ink-3">{step.why}</p>
                {step.arguments_summary && (
                  <p className="mono mt-0.5 text-[10.5px] text-ink-3/80">{step.arguments_summary}</p>
                )}
              </div>
            </li>
          ))}
        </ol>

        <p className="mt-4 text-[11.5px] leading-relaxed text-ink-3">
          {unknown
            ? 'One or more steps name a tool that does not exist — ask the agent to revise the plan.'
            : activePlan.writesState
              ? 'The write steps will each still ask for your approval as they run. Tell the agent to proceed, or adjust the plan first.'
              : 'This plan only reads. Tell the agent to proceed when you are ready.'}
        </p>
      </div>
    </div>
  );
}
