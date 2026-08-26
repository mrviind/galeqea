import { useCallback, useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import {
  AlertTriangle, ArrowRight, CheckCircle2, Circle, FileJson, KeyRound,
  MousePointerClick, Radio, ShieldAlert, Square, Upload,
} from 'lucide-react';
import { api } from '../lib/api';
import { relative } from '../lib/format';
import { useApp } from '../state';
import { Button, Chip, Empty, Panel, SectionTitle, Spinner } from '../components/primitives';

/**
 * Two ways to author tests without writing any: drive the browser and let
 * GaleQEA watch, or hand it an API specification.
 *
 * Both land in the same place — a PROPOSED test case waiting for review. That
 * is deliberate and it is shown on screen: having recorded a session is not the
 * same as having approved the test it produced.
 */
export default function Author() {
  const [tab, setTab] = useState<'record' | 'spec'>('record');
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1 border-b border-line px-1">
        {([
          ['record', 'Record a session', MousePointerClick],
          ['spec', 'Import an API spec', FileJson],
        ] as const).map(([key, label, Icon]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={clsx(
              'flex items-center gap-2 border-b-2 px-3.5 py-2.5 text-[13px] font-medium transition-colors',
              tab === key
                ? 'border-accent text-ink'
                : 'border-transparent text-ink-3 hover:text-ink-2',
            )}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}
      </div>
      {tab === 'record' ? <Recorder /> : <SpecImport />}
    </div>
  );
}

/* ========================================================================== */
/* Recording                                                                  */
/* ========================================================================== */
function Recorder() {
  const { project } = useApp();
  const [sessions, setSessions] = useState<any[]>([]);
  const [selected, setSelected] = useState<any | null>(null);
  const [startUrl, setStartUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    if (!project) return;
    const rows = await api.get<any[]>(`/api/projects/${project.id}/recordings`);
    setSessions(rows);
    setSelected((current: any) =>
      current ? rows.find((r) => r.id === current.id) ?? current : rows[0] ?? null);
  }, [project]);

  useEffect(() => { void load(); }, [load]);

  // A recording is live for as long as someone is using the browser, so the
  // list polls. Only while something is actually running: a finished session
  // does not change, and polling it forever would be noise on the network tab.
  const live = sessions.some((s) => s.status === 'recording' || s.status === 'starting');
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => { void load(); }, 2000);
    return () => clearInterval(timer);
  }, [live, load]);

  const start = async () => {
    if (!project) return;
    setBusy(true); setNotice('');
    try {
      const session = await api.post<any>(`/api/projects/${project.id}/recordings`, {
        start_url: startUrl || undefined,
      });
      setSelected(session);
      setNotice('A browser window has opened. Use the application as a tester would, then close the window to finish.');
      await load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'could not start recording');
    } finally { setBusy(false); }
  };

  const stop = async (id: string) => {
    if (!project) return;
    await api.post(`/api/projects/${project.id}/recordings/${id}/stop`);
    await load();
  };

  const promote = async (id: string) => {
    if (!project) return;
    setBusy(true);
    try {
      const result = await api.post<any>(`/api/projects/${project.id}/recordings/${id}/promote`);
      setNotice(`Filed ${result.test.key} — ${result.test.steps} steps, awaiting review in Approvals.`);
      await load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'could not promote');
    } finally { setBusy(false); }
  };

  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
      <div className="min-w-0 space-y-4">
        <Panel>
          <SectionTitle hint="a person drives">New recording</SectionTitle>
          <div className="space-y-3 px-4 pb-4">
            <label className="block">
              <span className="mb-1.5 block text-[11px] text-ink-3">
                Start URL <span className="text-ink-3/60">— defaults to the project environment</span>
              </span>
              <input
                value={startUrl}
                onChange={(e) => setStartUrl(e.target.value)}
                placeholder="https://staging.example.com/sign-in"
                className="w-full rounded-lg border border-line bg-surface-2 px-2.5 py-1.5 text-[12px] text-ink placeholder:text-ink-3/60 focus:border-line-strong focus:outline-none"
              />
            </label>
            <Button variant="primary" onClick={start} disabled={busy || !project} className="w-full">
              {busy ? <Spinner /> : <Radio size={14} />} Start recording
            </Button>
            <p className="text-[11px] leading-relaxed text-ink-3">
              A headed browser opens. Everything you click and type is captured as typed steps
              with a full locator ladder. <b className="text-ink-2">Alt+click</b> anything to
              record an assertion. Close the window when you are done.
            </p>
          </div>
        </Panel>

        <Panel>
          <SectionTitle hint={`${sessions.length}`}>Sessions</SectionTitle>
          <div className="max-h-[420px] overflow-y-auto pb-2">
            {sessions.length === 0 && (
              <p className="px-4 py-6 text-center text-[11px] text-ink-3">Nothing recorded yet.</p>
            )}
            {sessions.map((s) => (
              <button
                key={s.id}
                onClick={() => setSelected(s)}
                className={clsx(
                  'flex w-full flex-col gap-1 border-l-2 px-4 py-2.5 text-left transition-colors',
                  selected?.id === s.id
                    ? 'border-accent bg-surface-2'
                    : 'border-transparent hover:bg-surface-2/60',
                )}
              >
                <div className="flex items-center gap-2">
                  <RecordingStatus status={s.status} />
                  <span className="truncate text-[12px] font-medium text-ink">
                    {s.title || 'Untitled session'}
                  </span>
                </div>
                <span className="text-[11px] text-ink-3">
                  {s.stats?.steps != null
                    ? `${s.stats.steps} steps · ${s.stats.captured} captured`
                    : `${s.live_actions ?? 0} captured`}
                  {s.created_at && ` · ${relative(s.created_at)}`}
                </span>
              </button>
            ))}
          </div>
        </Panel>
      </div>

      <div className="min-w-0 space-y-4">
        {notice && (
          <Panel className="border-accent/30 bg-accent/5">
            <p className="px-4 py-2.5 text-[12px] text-ink-2">{notice}</p>
          </Panel>
        )}
        {selected ? (
          <SessionDetail session={selected} onStop={stop} onPromote={promote} busy={busy} />
        ) : (
          <Panel>
            <Empty
              icon={<MousePointerClick size={26} />}
              title="No recording selected"
              body="Start a session and use the application the way a tester would. GaleQEA captures each interaction as a typed step with its locator ladder, so the result is a maintainable test rather than a transcript."
            />
          </Panel>
        )}
      </div>
    </div>
  );
}

function RecordingStatus({ status }: { status: string }) {
  if (status === 'recording' || status === 'starting') {
    return <span className="dot h-1.5 w-1.5 shrink-0 bg-fail pulse-dot" />;
  }
  if (status === 'promoted') return <CheckCircle2 size={12} className="shrink-0 text-pass" />;
  if (status === 'error') return <AlertTriangle size={12} className="shrink-0 text-fail" />;
  return <Circle size={10} className="shrink-0 text-ink-3" />;
}

function SessionDetail({ session, onStop, onPromote, busy }: {
  session: any; onStop: (id: string) => void; onPromote: (id: string) => void; busy: boolean;
}) {
  const proposal = session.proposal ?? {};
  const steps: any[] = proposal.steps ?? [];
  const stats = session.stats ?? {};
  const recording = session.status === 'recording' || session.status === 'starting';

  return (
    <Panel>
      <SectionTitle
        hint={session.start_url}
        action={
          <div className="flex items-center gap-2">
            {recording && (
              <Button size="sm" variant="danger" onClick={() => onStop(session.id)}>
                <Square size={12} /> Stop
              </Button>
            )}
            {session.status === 'finished' && steps.length > 0 && (
              <Button size="sm" variant="primary" disabled={busy} onClick={() => onPromote(session.id)}>
                File for review <ArrowRight size={12} />
              </Button>
            )}
            {session.test_case_id && <Chip tone="good">Filed</Chip>}
          </div>
        }
      >
        {proposal.title || session.title || 'Recording'}
      </SectionTitle>

      {recording ? (
        <div className="flex items-center gap-3 border-t border-line px-4 py-8">
          <span className="dot h-2 w-2 bg-fail pulse-dot" />
          <div>
            <p className="text-[13px] text-ink">Recording — {session.live_actions ?? 0} interactions captured</p>
            <p className="text-[11px] text-ink-3">
              Close the browser window when you are finished, or press Stop. The step list is
              compiled once the session ends.
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-px border-y border-line bg-line xl:grid-cols-4">
            <Stat label="Captured" value={stats.captured ?? 0} />
            <Stat label="Steps" value={stats.steps ?? 0} />
            <Stat label="Assertions" value={stats.assertions ?? 0}
                  tone={stats.assertions ? undefined : 'warn'} />
            <Stat label="Secrets kept" value={stats.secrets_protected ?? 0} />
          </div>

          {proposal.rationale && (
            <p className="px-4 py-3 text-[11.5px] leading-relaxed text-ink-3">{proposal.rationale}</p>
          )}

          {!!(stats.compression_notes ?? []).length && (
            <div className="flex flex-wrap gap-1.5 px-4 pb-3">
              {stats.compression_notes.map((note: string) => (
                <Chip key={note}>{note}</Chip>
              ))}
            </div>
          )}

          <div className="border-t border-line">
            {steps.map((step, index) => <StepRow key={index} index={index} step={step} />)}
            {steps.length === 0 && (
              <p className="px-4 py-6 text-center text-[11px] text-ink-3">
                No interactions were captured in this session.
              </p>
            )}
          </div>
        </>
      )}
    </Panel>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: 'warn' }) {
  return (
    <div className="bg-surface px-4 py-2.5">
      <div className={clsx('text-[17px] font-semibold tabular-nums',
        tone === 'warn' && value === 0 ? 'text-flaky' : 'text-ink')}>{value}</div>
      <div className="text-[10.5px] uppercase tracking-wide text-ink-3">{label}</div>
    </div>
  );
}

const ASSERTION_ACTIONS = new Set(['expect_visible', 'expect_text', 'expect_url', 'expect_value']);

function StepRow({ index, step }: { index: number; step: any }) {
  const rung = (step.target?.ladder ?? [])[0];
  const isAssertion = ASSERTION_ACTIONS.has(step.action);
  const secret = !!step.options?.secret;
  return (
    <div className="flex items-start gap-3 border-b border-line/60 px-4 py-2 last:border-0">
      <span className="mono w-5 shrink-0 pt-0.5 text-right text-[11px] text-ink-3">{index + 1}</span>
      <span className={clsx(
        'mono mt-0.5 shrink-0 rounded border px-1.5 py-0.5 text-[10px]',
        isAssertion ? 'border-review/30 bg-review/10 text-review' : 'border-line bg-surface-3 text-ink-3',
      )}>
        {step.action}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[12px] text-ink-2">{step.intent}</p>
        {rung && (
          <p className="mono truncate text-[10.5px] text-ink-3">
            {rung.kind}
            {rung.value ? `=${rung.value}` : rung.name ? `[${rung.role}] ${rung.name}` : ''}
            {(step.target?.ladder?.length ?? 0) > 1 && (
              <span className="text-ink-3/60"> +{step.target.ladder.length - 1} fallback</span>
            )}
          </p>
        )}
      </div>
      {secret && (
        <span title="The value was never captured; the step carries a generator reference"
              className="mt-0.5 flex shrink-0 items-center gap-1 text-[10.5px] text-flaky">
          <KeyRound size={11} /> generated
        </span>
      )}
    </div>
  );
}

/* ========================================================================== */
/* API specification import                                                   */
/* ========================================================================== */
function SpecImport() {
  const { project } = useApp();
  const [text, setText] = useState('');
  const [analysis, setAnalysis] = useState<any | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);

  const send = async (path: 'analyze' | 'import', file?: File) => {
    if (!project) return;
    setBusy(true); setNotice('');
    try {
      const form = new FormData();
      if (file) form.append('file', file);
      else form.append('text', text);
      const result = await api.upload<any>(`/api/projects/${project.id}/api-spec/${path}`, form);
      setAnalysis(result);
      if (path === 'import') {
        setNotice(result.unchanged
          ? 'This specification was already imported — nothing was created a second time.'
          : `Filed ${result.created.length} test case(s) for review.`);
      }
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'could not read the specification');
      if (path === 'analyze') setAnalysis(null);
    } finally { setBusy(false); }
  };

  const summary = analysis?.summary;

  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
      <Panel className="min-w-0 self-start">
        <SectionTitle hint="OpenAPI 3.x, JSON or YAML">Specification</SectionTitle>
        <div className="space-y-3 px-4 pb-4">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={'openapi: 3.0.3\ninfo:\n  title: Orders API\npaths:\n  /orders:\n    get: …'}
            spellCheck={false}
            className="mono h-44 w-full resize-y rounded-lg border border-line bg-surface-2 p-2.5 text-[11px] leading-relaxed text-ink placeholder:text-ink-3/50 focus:border-line-strong focus:outline-none"
          />
          <div className="flex gap-2">
            <Button onClick={() => send('analyze')} disabled={busy || !text.trim()} className="flex-1">
              {busy ? <Spinner /> : null} Analyse
            </Button>
            <Button variant="ghost" onClick={() => fileInput.current?.click()} disabled={busy}>
              <Upload size={14} /> File
            </Button>
            <input
              ref={fileInput} type="file" hidden
              accept=".json,.yaml,.yml,application/json,text/yaml"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void send('analyze', f); e.target.value = ''; }}
            />
          </div>
          <p className="text-[11px] leading-relaxed text-ink-3">
            Analysing writes nothing. Contract, boundary, authentication and hostile-input cases
            are derived from the schema by rule — no model is used, and none is needed.
          </p>
        </div>
      </Panel>

      <div className="min-w-0 space-y-4">
        {notice && (
          <Panel className="border-accent/30 bg-accent/5">
            <p className="px-4 py-2.5 text-[12px] text-ink-2">{notice}</p>
          </Panel>
        )}

        {!analysis && (
          <Panel>
            <Empty
              icon={<FileJson size={26} />}
              title="No specification loaded"
              body="An OpenAPI document already states which parameters are required, what their bounds are and what each response must look like. That is most of a test suite, and none of it needs a model to extract."
            />
          </Panel>
        )}

        {analysis && (
          <>
            <Panel>
              <SectionTitle
                hint={analysis.spec.version ? `v${analysis.spec.version}` : undefined}
                action={
                  <Button
                    variant="primary" size="sm" disabled={busy}
                    onClick={() => send('import', undefined)}
                  >
                    File {summary.proposals} test{summary.proposals === 1 ? '' : 's'} for review
                  </Button>
                }
              >
                {analysis.spec.title}
              </SectionTitle>
              <div className="grid grid-cols-2 gap-px border-y border-line bg-line xl:grid-cols-4">
                <Stat label="Operations" value={summary.operations} />
                <Stat label="Test cases" value={summary.proposals} />
                <Stat label="Secured" value={summary.secured_operations} />
                <Stat label="No schema" value={summary.operations_without_response_schema}
                      tone={summary.operations_without_response_schema ? "warn" : undefined} />
              </div>
              <div className="flex flex-wrap gap-1.5 px-4 py-3">
                {Object.entries(summary.by_technique).map(([technique, count]) => (
                  <Chip key={technique} tone="brand">{technique} · {String(count)}</Chip>
                ))}
              </div>
              {analysis.base_url ? (
                <p className="border-t border-line px-4 py-2.5 text-[11px] text-ink-3">
                  Tests will run against <span className="mono text-ink-2">{analysis.base_url}</span>
                  {' '}— the project environment, not the <span className="mono">servers</span> entry
                  in the specification.
                </p>
              ) : (
                <p className="border-t border-line px-4 py-2.5 text-[11px] text-flaky">
                  This project has no environment URL configured, so generated tests have nowhere
                  to point. Set one in Settings before running them.
                </p>
              )}
            </Panel>

            {!!analysis.spec_issues?.length && (
              <Panel className="border-flaky/30">
                <SectionTitle hint="defects in the specification, not in the tests">
                  What limits the coverage
                </SectionTitle>
                <ul className="space-y-1.5 px-4 pb-4">
                  {analysis.spec_issues.map((issue: string) => (
                    <li key={issue} className="flex gap-2 text-[11.5px] leading-relaxed text-ink-2">
                      <AlertTriangle size={13} className="mt-0.5 shrink-0 text-flaky" />
                      {issue}
                    </li>
                  ))}
                </ul>
              </Panel>
            )}

            {analysis.injection_scan?.suspicious && (
              <Panel className="border-fail/30">
                <SectionTitle hint="surfaced, never silently stripped">
                  This document contains agent-directed text
                </SectionTitle>
                <ul className="space-y-1.5 px-4 pb-4">
                  {analysis.injection_scan.findings.slice(0, 5).map((f: any, i: number) => (
                    <li key={i} className="flex gap-2 text-[11.5px] leading-relaxed text-ink-2">
                      <ShieldAlert size={13} className="mt-0.5 shrink-0 text-fail" />
                      <span><b>{f.kind}</b> — <span className="mono text-ink-3">{f.excerpt}</span></span>
                    </li>
                  ))}
                </ul>
              </Panel>
            )}

            <Panel>
              <SectionTitle hint={`${analysis.proposals.length}`}>Proposed tests</SectionTitle>
              <div className="max-h-[520px] overflow-y-auto border-t border-line">
                {analysis.proposals.map((proposal: any, index: number) => (
                  <ProposalRow key={index} proposal={proposal} />
                ))}
              </div>
            </Panel>
          </>
        )}
      </div>
    </div>
  );
}

function ProposalRow({ proposal }: { proposal: any }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-line/60 last:border-0">
      <button onClick={() => setOpen(!open)} className="flex w-full items-start gap-3 px-4 py-2.5 text-left hover:bg-surface-2/60">
        <Chip tone={proposal.technique === 'security' ? 'danger' : 'neutral'} className="mt-px shrink-0">
          {proposal.technique}
        </Chip>
        <span className="min-w-0 flex-1 truncate text-[12px] text-ink-2">{proposal.title}</span>
        <span className="mono shrink-0 text-[10.5px] text-ink-3">{proposal.steps.length} step{proposal.steps.length === 1 ? '' : 's'}</span>
      </button>
      {open && (
        <div className="space-y-2 bg-surface-2/40 px-4 pb-3 pt-1">
          <p className="text-[11.5px] leading-relaxed text-ink-3">{proposal.rationale}</p>
          {proposal.steps.map((step: any, index: number) => (
            <div key={index} className="mono rounded-md border border-line bg-canvas p-2 text-[10.5px] leading-relaxed text-ink-3">
              <div className="text-ink-2">
                <span className="text-accent">{step.value.method}</span> {step.value.url}
              </div>
              {step.value.body && <div className="truncate">body {JSON.stringify(step.value.body)}</div>}
              <div>
                expect{' '}
                {step.value.expect_status ?? (step.value.expect_status_in ?? []).join(' | ')}
                {step.value.expect_schema && ' · body conforms to schema'}
                {step.value.forbid_body_contains && ' · not reflected'}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
