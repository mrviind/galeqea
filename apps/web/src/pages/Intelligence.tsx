import { useCallback, useEffect, useState } from 'react';
import clsx from 'clsx';
import {
  Activity, Boxes, Bug, Check, Compass, ExternalLink, Eye, Image as ImageIcon,
  Layers, Loader2, Play, Wrench, X, Zap,
} from 'lucide-react';
import { api } from '../lib/api';
import { relative } from '../lib/format';
import { useApp, useEvents } from '../state';
import { Button, Chip, Empty, Meter, Panel, SectionTitle, StatusPill } from '../components/primitives';

const TABS = [
  { id: 'flaky', label: 'Flakiness', icon: Activity },
  { id: 'heals', label: 'Healing', icon: Wrench },
  { id: 'explore', label: 'Explore', icon: Compass },
  { id: 'visual', label: 'Visual', icon: Eye },
  { id: 'anomalies', label: 'Anomalies', icon: Zap },
  { id: 'appmodel', label: 'App Model', icon: Layers },
] as const;

export default function Intelligence() {
  const { project } = useApp();
  const [tab, setTab] = useState<(typeof TABS)[number]['id']>('flaky');
  const [flaky, setFlaky] = useState<any>({ flaky: [], quarantine_candidates: [] });
  const [heals, setHeals] = useState<any[]>([]);
  const [anomalies, setAnomalies] = useState<any[]>([]);
  const [appModel, setAppModel] = useState<any>({ screens: [], fragile_elements: [] });
  const [sessions, setSessions] = useState<any[]>([]);
  const [findings, setFindings] = useState<any[]>([]);
  const [visual, setVisual] = useState<any>({ comparisons: [], baselines: [] });

  const load = useCallback(async () => {
    if (!project) return;
    const [f, h, a, m, e, fi, v] = await Promise.all([
      api.get<any>(`/api/projects/${project.id}/flaky`),
      api.get<any[]>(`/api/projects/${project.id}/heals?status=proposed`),
      api.get<any[]>(`/api/projects/${project.id}/anomalies`),
      api.get<any>(`/api/projects/${project.id}/app-model`),
      api.get<any[]>(`/api/projects/${project.id}/explore`),
      api.get<any[]>(`/api/projects/${project.id}/findings?status=new`),
      api.get<any>(`/api/projects/${project.id}/visual?status=new`),
    ]);
    setFlaky(f); setHeals(h); setAnomalies(a); setAppModel(m);
    setSessions(e); setFindings(fi); setVisual(v);
  }, [project]);

  useEffect(() => { void load(); }, [load]);
  useEvents(['run.finished', 'heal.proposed', 'anomaly.detected', 'notification'], () => void load());

  const decideHeal = async (id: string, decision: 'approve' | 'reject') => {
    if (!project) return;
    await api.post(`/api/projects/${project.id}/heals/${id}/decide`, { decision });
    await load();
  };

  return (
    <div className="space-y-3 p-3">
      <div className="flex gap-1">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={clsx(
              'flex items-center gap-1.5 border px-3 py-1.5 text-[12px] transition',
              tab === id
                ? 'border-accent/30 bg-accent/10 text-accent'
                : 'border-line bg-surface-2 text-ink-3 hover:text-ink-2',
            )}
          >
            <Icon size={12} /> {label}
            {id === 'heals' && heals.length > 0 && (
              <span className="bg-flaky px-1.5 text-[10px] font-semibold text-canvas">{heals.length}</span>
            )}
          </button>
        ))}
      </div>

      {tab === 'flaky' && (
        <Panel className="overflow-hidden">
          <SectionTitle hint="score and confidence are separate — a score with no history behind it means little">
            Test stability
          </SectionTitle>
          <div className="border-t border-line">
            {flaky.flaky.length === 0 && (
              <Empty title="No instability detected" body="Scores appear once tests have run a few times." />
            )}
            {flaky.flaky.map((row: any) => (
              <div key={row.test_case_id ?? row.key} className="border-b border-line/60 px-4 py-2.5 last:border-0">
                <div className="flex items-center gap-2">
                  <span className="mono shrink-0 text-[11px] text-ink-3">{row.key}</span>
                  <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink-2">{row.title}</span>
                  <Chip tone={row.recommendation === 'quarantine' ? 'danger' : row.recommendation === 'investigate' ? 'warn' : 'neutral'}>
                    {row.recommendation}
                  </Chip>
                  <span className="mono w-10 shrink-0 text-right text-[11px] text-flaky">
                    {Math.round(row.score * 100)}%
                  </span>
                </div>
                <div className="mt-1.5 flex items-center gap-3">
                  <div className="flex-1">
                    <Meter value={row.score * 100} tone="flaky" />
                  </div>
                  <span className="shrink-0 text-[10px] text-ink-3">
                    confidence {Math.round(row.confidence * 100)}% · {row.runs} runs · {Math.round(row.pass_rate * 100)}% pass
                  </span>
                </div>
                <ul className="mt-1 space-y-0.5">
                  {(row.reasons ?? []).map((reason: string, i: number) => (
                    <li key={i} className="text-[11px] text-ink-3">· {reason}</li>
                  ))}
                </ul>
                {row.window?.length > 0 && (
                  <div className="mt-1.5 flex gap-px">
                    {row.window.slice(0, 30).reverse().map((w: any, i: number) => (
                      <span
                        key={i}
                        title={`${w.status} · ${w.duration_ms}ms`}
                        className={clsx(
                          'h-3 w-1.5',
                          w.status === 'passed' ? 'bg-pass/70'
                            : w.status === 'failed' || w.status === 'error' ? 'bg-fail/70'
                            : 'bg-ink-3/40',
                        )}
                      />
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Panel>
      )}

      {tab === 'heals' && (
        <Panel className="overflow-hidden">
          <SectionTitle hint="a heal repairs every test that references the element — never applied silently">
            Proposed heals
          </SectionTitle>
          <div className="border-t border-line">
            {heals.length === 0 && (
              <Empty
                icon={<Wrench size={20} />}
                title="No heals waiting"
                body="When a locator breaks, QE Agent re-identifies the element and proposes the fix here with its evidence."
              />
            )}
            {heals.map((heal) => (
              <div key={heal.id} className="border-b border-line/60 p-4 last:border-0">
                <div className="flex items-center gap-2">
                  <Chip tone={heal.strategy === 'semantic_llm' ? 'brand' : 'neutral'}>{heal.strategy}</Chip>
                  <span className="mono text-[11px] text-pass">{Math.round(heal.score * 100)}% confidence</span>
                  {/* The number that makes the App Model worth having: one
                      review, many tests fixed. */}
                  {heal.affected_tests > 1 && (
                    <Chip tone="brand">repairs {heal.affected_tests} tests</Chip>
                  )}
                  <span className="ml-auto text-[10.5px] text-ink-3">{relative(heal.at)}</span>
                </div>
                <div className="rounded-md mono mt-2 space-y-1 border border-line bg-canvas p-2.5 text-[11px]">
                  <p className="text-fail line-through">− {heal.old_locator}</p>
                  <p className="text-pass">+ {heal.new_locator}</p>
                </div>
                <p className="mt-1.5 text-[11.5px] text-ink-3">{heal.evidence?.reason}</p>
                {heal.candidates?.length > 0 && (
                  <div className="mt-1.5 space-y-0.5">
                    <p className="text-[10.5px] text-ink-3">Candidates considered:</p>
                    {heal.candidates.slice(0, 3).map((c: any, i: number) => (
                      <p key={i} className="mono text-[10px] text-ink-3">
                        {(c.score ?? 0).toFixed(2)} · {c.role} “{c.name}” {c.testId ? `[${c.testId}]` : ''}
                      </p>
                    ))}
                  </div>
                )}
                <div className="mt-2.5 flex items-center gap-1.5">
                  <Button size="sm" variant="primary" onClick={() => decideHeal(heal.id, 'approve')}>
                    <Check size={11} /> Apply to App Model
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => decideHeal(heal.id, 'reject')}>
                    <X size={11} /> Reject
                  </Button>
                  {heal.affected_tests > 1 && (
                    <span className="text-[10.5px] text-ink-3">
                      one approval fixes all {heal.affected_tests}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {tab === 'explore' && (
        <ExploreTab sessions={sessions} findings={findings} onChange={load} />
      )}

      {tab === 'visual' && (
        <VisualTab data={visual} onChange={load} />
      )}

      {tab === 'anomalies' && (
        <Panel className="overflow-hidden">
          <SectionTitle hint="robust z-score over median and MAD, so one slow run cannot blind the detector">
            Anomalies
          </SectionTitle>
          <div className="border-t border-line">
            {anomalies.length === 0 && <Empty title="No anomalies detected" />}
            {anomalies.map((a) => (
              <div key={a.id} className="flex items-baseline gap-3 border-b border-line/60 px-4 py-2 last:border-0">
                <Chip tone={a.severity === 'critical' ? 'danger' : 'warn'}>{a.sigma}σ</Chip>
                <span className="min-w-0 flex-1 truncate text-[12px] text-ink-2">
                  {a.detail?.label} · {a.detail?.note}
                </span>
                <span className="shrink-0 text-[10.5px] text-ink-3">{relative(a.at)}</span>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {tab === 'appmodel' && (
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,340px)]">
          <Panel className="overflow-hidden">
            <SectionTitle hint={`${appModel.element_count ?? 0} element(s) · learned from runs, not curated`}>
              Application model
            </SectionTitle>
            <div className="max-h-[520px] overflow-y-auto border-t border-line">
              {(appModel.screens ?? []).length === 0 && (
                <Empty
                  icon={<Boxes size={20} />}
                  title="No app model yet"
                  body="Run a test and this fills itself in. QE Agent records every screen and element a run touches, so healing repairs an element once — for every test that uses it."
                />
              )}
              {(appModel.screens ?? []).map((screen: any) => (
                <div key={screen.id} className="border-b border-line/60 px-4 py-2.5 last:border-0">
                  <div className="flex items-baseline gap-2">
                    <span className="text-[12.5px] font-medium text-ink">{screen.name}</span>
                    <span className="mono text-[10.5px] text-ink-3">{screen.url_pattern}</span>
                    <span className="ml-auto text-[10.5px] text-ink-3">{screen.visit_count} visits</span>
                  </div>
                  <div className="mt-1 space-y-0.5">
                    {(screen.elements ?? []).map((el: any) => (
                      <div key={el.id} className="flex items-baseline gap-2 text-[11px]">
                        <span className="shrink-0 text-ink-3">{el.role}</span>
                        <span className="min-w-0 flex-1 truncate text-ink-2">{el.accessible_name || el.intent}</span>
                        {el.heal_count > 0 && <Chip tone="warn">healed {el.heal_count}×</Chip>}
                        <span className="mono shrink-0 text-[10px] text-ink-3">
                          stability {Math.round(el.stability * 100)}%
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </Panel>

          <Panel className="overflow-hidden">
            <SectionTitle hint="churn hot spots">Fragile elements</SectionTitle>
            <div className="border-t border-line">
              {(appModel.fragile_elements ?? []).length === 0 && (
                <Empty title="Nothing fragile yet" />
              )}
              {(appModel.fragile_elements ?? []).map((el: any) => (
                <div key={el.id} className="border-b border-line/60 px-4 py-2 last:border-0">
                  <p className="truncate text-[12px] text-ink-2">{el.accessible_name || el.intent}</p>
                  <p className="text-[10.5px] text-ink-3">
                    healed {el.heal_count}× · stability {Math.round(el.stability * 100)}%
                  </p>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}


// --------------------------------------------------------------------------- //
/**
 * Autonomous exploratory testing.
 *
 * Exploration answers a different question from a test: not "does this still do
 * what we agreed?" but "what does this do that we never agreed about?" — so the
 * output is findings to triage, not a verdict, and it is kept out of the
 * pass-rate statistics where it would mean nothing.
 */
function ExploreTab({
  sessions, findings, onChange,
}: { sessions: any[]; findings: any[]; onChange: () => void }) {
  const { project } = useApp();
  const [charter, setCharter] = useState('');
  const [steps, setSteps] = useState(30);
  const [transactional, setTransactional] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [open, setOpen] = useState<string | null>(null);

  const running = sessions.find((s) => s.status === 'running');

  const start = async () => {
    if (!project) return;
    setBusy(true); setError('');
    try {
      await api.post(`/api/projects/${project.id}/explore`, {
        charter: charter || 'Explore the application and report anything surprising.',
        max_steps: steps,
        allow_transactional: transactional,
      });
      setCharter('');
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'could not start exploration');
    } finally { setBusy(false); }
  };

  const decide = async (id: string, decision: string) => {
    if (!project) return;
    await api.post(`/api/projects/${project.id}/findings/${id}/decide`, { decision });
    onChange();
  };

  const SEVERITY: Record<string, 'danger' | 'warn' | 'neutral'> = {
    high: 'danger', medium: 'warn', low: 'neutral',
  };

  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,380px)]">
      <Panel className="overflow-hidden">
        <SectionTitle hint={`${findings.length} awaiting triage · worst first`}>
          Findings
        </SectionTitle>
        <div className="border-t border-line">
          {findings.length === 0 && (
            <Empty
              icon={<Bug size={20} />}
              title="Nothing found yet"
              body="Give the explorer a charter and a step budget. It drives a real browser, reports what it finds, and refuses destructive controls outright."
            />
          )}
          {findings.map((f) => (
            <div key={f.id} className="border-b border-line/60 px-4 py-3 last:border-0">
              <div className="flex flex-wrap items-center gap-2">
                <Chip tone={SEVERITY[f.severity] ?? 'neutral'}>{f.severity}</Chip>
                <code className="mono text-[10.5px] text-ink-3">{f.kind}</code>
                {f.occurrences > 1 && <Chip tone="warn">seen {f.occurrences}×</Chip>}
                {f.found_by === 'deterministic' && <Chip>no model needed</Chip>}
                <span className="ml-auto mono text-[10px] text-ink-3">
                  {Math.round(f.confidence * 100)}% confident
                </span>
              </div>
              <p className="mt-1.5 text-[12.5px] font-medium text-ink">{f.title}</p>
              <p className="mt-0.5 whitespace-pre-line text-[11.5px] leading-relaxed text-ink-2">
                {f.detail}
              </p>
              {f.url && (
                <a
                  href={f.url} target="_blank" rel="noreferrer"
                  className="mono mt-1 inline-flex items-center gap-1 text-[10.5px] text-accent hover:underline"
                >
                  <ExternalLink size={9} /> {f.url}
                </a>
              )}

              {f.reproduction?.length > 0 && (
                <button
                  onClick={() => setOpen(open === f.id ? null : f.id)}
                  className="mt-1.5 block text-[10.5px] text-ink-3 transition-colors hover:text-ink-2"
                >
                  {open === f.id ? 'Hide' : 'Show'} the {f.reproduction.length} steps that reach this
                </button>
              )}
              {open === f.id && (
                <ol className="mt-1 space-y-0.5 rounded-lg border border-line bg-canvas p-2">
                  {f.reproduction.map((r: any, i: number) => (
                    <li key={i} className="mono text-[10.5px] text-ink-3">
                      {String(i + 1).padStart(2, '0')} {r.action} {r.target}
                      {r.value ? ` = ${JSON.stringify(r.value)}` : ''}
                    </li>
                  ))}
                </ol>
              )}

              <div className="mt-2 flex gap-1.5">
                <Button size="sm" variant="primary" onClick={() => decide(f.id, 'promote')}>
                  <Check size={11} /> Promote to a test
                </Button>
                <Button size="sm" variant="ghost" onClick={() => decide(f.id, 'accept')}>
                  Accept
                </Button>
                <Button size="sm" variant="subtle" onClick={() => decide(f.id, 'dismiss')}>
                  <X size={11} /> Not a defect
                </Button>
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <div className="space-y-3">
        <Panel glow={Boolean(running)} className="overflow-hidden">
          <SectionTitle hint="a mission, not a script">New session</SectionTitle>
          <div className="space-y-2 px-4 pb-4">
            <textarea
              rows={2}
              value={charter}
              onChange={(e) => setCharter(e.target.value)}
              placeholder="Charter — e.g. probe the checkout form for input it silently discards"
              className="w-full resize-none rounded-lg border border-line bg-surface-2 px-2.5 py-2
                         text-[12px] outline-none placeholder:text-ink-3 focus:border-accent"
            />
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5 text-[11px] text-ink-3">
                budget
                <input
                  type="number" min={4} max={120} value={steps}
                  onChange={(e) => setSteps(Number(e.target.value))}
                  className="w-16 rounded-lg border border-line bg-surface-2 px-2 py-1 text-[11px]
                             outline-none focus:border-accent"
                />
                steps
              </label>
            </div>
            <label className="flex items-start gap-2 text-[11px] leading-relaxed text-ink-3">
              <input
                type="checkbox" checked={transactional}
                onChange={(e) => setTransactional(e.target.checked)}
                className="mt-0.5 accent-ink"
              />
              <span>
                Allow transactional controls (pay, place order, transfer).
                <span className="block text-ink-3/80">
                  Never enable this against production. Destructive controls — delete,
                  revoke, sign out — stay blocked either way.
                </span>
              </span>
            </label>
            {error && <p className="text-[11px] text-fail">{error}</p>}
            <Button variant="primary" onClick={start} disabled={busy || Boolean(running)}>
              {busy || running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
              {running ? 'Exploring…' : 'Start exploring'}
            </Button>
          </div>
        </Panel>

        <Panel className="overflow-hidden">
          <SectionTitle hint={`${sessions.length} session(s)`}>Sessions</SectionTitle>
          <div className="max-h-[320px] overflow-y-auto border-t border-line">
            {sessions.length === 0 && <Empty title="No sessions yet" />}
            {sessions.map((s) => (
              <div key={s.id} className="border-b border-line/60 px-4 py-2.5 last:border-0">
                <div className="flex items-center gap-2">
                  <StatusPill status={s.status === 'completed' ? 'passed' : s.status === 'error' ? 'failed' : 'running'}
                              live={s.status === 'running'} />
                  <Chip>{s.strategy}</Chip>
                  <span className="mono ml-auto text-[10px] text-ink-3">
                    {s.steps_taken}/{s.max_steps} steps
                  </span>
                </div>
                <p className="mt-1 text-[11.5px] text-ink-2">{s.charter}</p>
                {s.summary && <p className="mt-0.5 text-[10.5px] text-ink-3">{s.summary}</p>}
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}


// --------------------------------------------------------------------------- //
/**
 * Visual regression review.
 *
 * Structural comparison leads, pixels support it. The case for that ordering is
 * visible in the numbers: removing a required field from a checkout form
 * changes under 1% of the image, so any pixel-percentage threshold loose enough
 * to tolerate anti-aliasing is also loose enough to miss it. What the
 * accessibility tree gained and lost is the part that means something in words.
 */
function VisualTab({ data, onChange }: { data: any; onChange: () => void }) {
  const { project } = useApp();
  const comparisons: any[] = data.comparisons ?? [];
  const baselines: any[] = data.baselines ?? [];
  const [selected, setSelected] = useState<string | null>(comparisons[0]?.id ?? null);
  const [view, setView] = useState<'side' | 'diff' | 'baseline'>('side');
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const current = comparisons.find((c) => c.id === selected) ?? comparisons[0];

  const decide = async (decision: 'accept' | 'reject') => {
    if (!project || !current) return;
    setBusy(true); setError('');
    try {
      await api.post(`/api/projects/${project.id}/visual/${current.id}/decide`, {
        decision, comment,
      });
      setComment('');
      setSelected(null);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'could not record that decision');
    } finally { setBusy(false); }
  };

  const SEVERITY: Record<string, 'danger' | 'warn' | 'neutral'> = {
    breaking: 'danger', notable: 'warn', cosmetic: 'neutral', none: 'neutral',
  };

  const src = (side: string) =>
    `/api/projects/${project?.id}/visual/${current?.id}/image?side=${side}`;

  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,300px)_minmax(0,1fr)]">
      <div className="space-y-3">
        <Panel className="overflow-hidden">
          <SectionTitle hint="worst first">Awaiting review</SectionTitle>
          <div className="max-h-[340px] overflow-y-auto border-t border-line">
            {comparisons.length === 0 && (
              <Empty
                icon={<ImageIcon size={20} />}
                title="Nothing to review"
                body="Add a snapshot step to a test. The first run establishes the baseline; later runs are compared against it and only real changes land here."
              />
            )}
            {comparisons.map((c) => (
              <button
                key={c.id}
                onClick={() => { setSelected(c.id); setView('side'); }}
                className={clsx(
                  'block w-full border-b border-line/60 px-4 py-2.5 text-left transition last:border-0',
                  current?.id === c.id ? 'bg-surface-2' : 'hover:bg-surface-2/60',
                )}
              >
                <div className="flex items-center gap-2">
                  <Chip tone={SEVERITY[c.severity] ?? 'neutral'}>{c.severity}</Chip>
                  <span className="truncate text-[12.5px] text-ink">{c.name}</span>
                </div>
                <p className="mt-0.5 text-[10.5px] text-ink-3">
                  {c.changed_pct}% of pixels · {relative(c.at)}
                </p>
              </button>
            ))}
          </div>
        </Panel>

        <Panel className="overflow-hidden">
          <SectionTitle hint="versioned, never overwritten">Baselines</SectionTitle>
          <div className="max-h-[220px] overflow-y-auto border-t border-line">
            {baselines.length === 0 && <Empty title="No baselines yet" />}
            {baselines.map((b) => (
              <div key={`${b.name}-${b.version}`} className="border-b border-line/60 px-4 py-2 last:border-0">
                <div className="flex items-baseline gap-2">
                  <span className="min-w-0 flex-1 truncate text-[12px] text-ink-2">{b.name}</span>
                  <Chip>v{b.version}</Chip>
                  <span className="text-[10px] text-ink-3">{relative(b.at)}</span>
                </div>
                <p className="mt-0.5 text-[10px] text-ink-3">
                  {b.approved_by ? 'approved by a reviewer' : 'established automatically on first run'}
                  {b.superseded > 0 && ` · ${b.superseded} earlier version(s) kept`}
                </p>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      {current ? (
        <Panel className="overflow-hidden">
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
            <Chip tone={SEVERITY[current.severity] ?? 'neutral'}>{current.severity}</Chip>
            <h2 className="text-[13px] font-semibold text-ink">{current.name}</h2>
            {current.judged_by === 'model' && <Chip tone="brand">model-judged</Chip>}
            {current.dimensions_changed && <Chip tone="warn">size changed</Chip>}
            <div className="ml-auto flex gap-1">
              {(['side', 'diff', 'baseline'] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={clsx(
                    'rounded-md px-2 py-1 text-[11px] transition',
                    view === v ? 'bg-surface-3 text-ink' : 'text-ink-3 hover:text-ink-2',
                  )}
                >
                  {v === 'side' ? 'Side by side' : v === 'diff' ? 'Changes highlighted' : 'Baseline only'}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-3 p-4">
            <p className="text-[12.5px] leading-relaxed text-ink-2">{current.summary}</p>

            {current.structural?.lost_controls?.length > 0 && (
              <div className="rounded-lg border border-fail/30 bg-fail/[0.07] p-2.5">
                <p className="text-[12px] font-medium text-fail">
                  A user can no longer do something they previously could
                </p>
                <ul className="mt-1 space-y-0.5">
                  {current.structural.lost_controls.map((control: string) => (
                    <li key={control} className="mono text-[11px] text-ink-2">− {control}</li>
                  ))}
                </ul>
                <p className="mt-1.5 text-[10.5px] leading-relaxed text-ink-3">
                  This changed only {current.changed_pct}% of the image — small enough that
                  a pixel threshold loose enough to ignore anti-aliasing would have missed it.
                </p>
              </div>
            )}

            {/* --- the images ------------------------------------------- */}
            {view === 'side' ? (
              <div className="grid gap-2 md:grid-cols-2">
                {[['baseline', 'Baseline'], ['candidate', 'This run']].map(([side, label]) => (
                  <figure key={side} className="space-y-1">
                    <figcaption className="text-[10.5px] uppercase tracking-wide text-ink-3">{label}</figcaption>
                    <img
                      src={src(side)} alt={`${label} screenshot of ${current.name}`}
                      className="w-full rounded-lg border border-line bg-canvas"
                    />
                  </figure>
                ))}
              </div>
            ) : (
              <figure className="space-y-1">
                <figcaption className="text-[10.5px] uppercase tracking-wide text-ink-3">
                  {view === 'diff' ? 'This run, changed regions boxed' : 'Baseline'}
                </figcaption>
                <img
                  src={src(view === 'diff' && current.has_diff_image ? 'diff' : view === 'diff' ? 'candidate' : 'baseline')}
                  alt={`${view} view of ${current.name}`}
                  className="w-full rounded-lg border border-line bg-canvas"
                />
              </figure>
            )}

            {/* --- what the accessibility tree says --------------------- */}
            <div className="grid gap-2 md:grid-cols-2">
              <StructuralList label="Removed" tone="fail" items={current.structural?.removed ?? []} />
              <StructuralList label="Added" tone="pass" items={current.structural?.added ?? []} />
            </div>

            {current.regions?.length > 0 && (
              <p className="text-[10.5px] text-ink-3">
                {current.regions.length} changed region(s):{' '}
                {current.regions.slice(0, 4).map((r: any) => `${r.w}×${r.h} at (${r.x}, ${r.y})`).join(' · ')}
              </p>
            )}

            {error && <p className="text-[11.5px] text-fail">{error}</p>}

            <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
              <input
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="Reason (recorded in the audit ledger)…"
                className="min-w-0 flex-1 rounded-lg border border-line bg-surface-2 px-2.5 py-1.5
                           text-[12px] outline-none placeholder:text-ink-3 focus:border-accent"
              />
              <Button variant="primary" size="sm" onClick={() => decide('accept')} disabled={busy}>
                <Check size={11} /> This is correct — make it the baseline
              </Button>
              <Button variant="ghost" size="sm" onClick={() => decide('reject')} disabled={busy}>
                <X size={11} /> This is a defect
              </Button>
            </div>
            <p className="text-[10.5px] leading-relaxed text-ink-3">
              Accepting records a new baseline version; the previous one is kept, so
              “when did it start looking like that?” stays answerable. Rejecting leaves
              the baseline untouched.
            </p>
          </div>
        </Panel>
      ) : (
        <Panel>
          <Empty
            icon={<Eye size={22} />}
            title="No comparison selected"
            body="Screens that did not change are recorded as auto-passed and stay out of this queue — a review list padded with non-events is one people stop reading."
          />
        </Panel>
      )}
    </div>
  );
}

function StructuralList({ label, tone, items }: { label: string; tone: 'fail' | 'pass'; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="rounded-lg border border-line bg-surface-2 p-2.5">
      <p className={clsx('text-[10.5px] uppercase tracking-wide', tone === 'fail' ? 'text-fail' : 'text-pass')}>
        {label} ({items.length})
      </p>
      <ul className="mt-1 max-h-32 space-y-0.5 overflow-y-auto">
        {items.map((item) => (
          <li key={item} className="mono truncate text-[10.5px] text-ink-3">
            {tone === 'fail' ? '−' : '+'} {item}
          </li>
        ))}
      </ul>
    </div>
  );
}
