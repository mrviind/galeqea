/**
 * Rich chat blocks.
 *
 * The agent returns typed payloads rather than markdown, so the chat can render
 * real controls - run buttons, approval prompts, evidence-cited RCA - instead of
 * text describing controls that live somewhere else. This is what makes the
 * conversation the primary interface rather than a commentary on the UI.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import {
  AlertTriangle, Ban, Calendar, Check, ChevronDown, ClipboardList, Clock, Compass, Download,
  FileText, FlaskConical, GitCompare, Info, Lock, PlayCircle, RotateCw, Rocket, Server as ServerIcon,
  Share2, ShieldAlert, ShieldCheck, Sparkles, Target, X,
} from 'lucide-react';
import { api, type ChatBlock } from '../lib/api';
import { RISK_COLOR, duration, statusMeta } from '../lib/format';
import { renderMarkdown } from '../lib/markdown';
import { useApp } from '../state';
import { runCommand } from '../workspace';
import { Button, Chip, Meter, StatusPill } from './primitives';
import { JourneyRail, stageLabel } from './JourneyRail';
import { ReportExport } from './ReportExport';
import { ConnectForm } from './IntegrationsPanel';

export function Blocks({ blocks }: { blocks: ChatBlock[] }) {
  return (
    <>
      {blocks.map((block, i) => (
        <Block key={`${block.type}-${i}`} block={block} />
      ))}
    </>
  );
}

function Block({ block }: { block: ChatBlock }) {
  switch (block.type) {
    case 'run_controls': return <RunControls block={block} />;
    case 'run_summary': return <RunSummaryBlock block={block} />;
    case 'test_table': return <TestTable block={block} />;
    case 'coverage': return <CoverageBlock block={block} />;
    case 'flaky_table': return <FlakyBlock block={block} />;
    case 'selection': return <SelectionBlock block={block} />;
    case 'approval_prompt': return <ApprovalPrompt block={block} />;
    case 'approval_list': return <ApprovalList block={block} />;
    case 'approval_pending': return <ApprovalPending block={block} />;
    case 'integration_connect': return <ConnectForm providerName={block.provider} baseUrl={block.base_url} />;
    case 'jira_import': return <JiraImportCard block={block} />;
    case 'exploratory_session_card': return <ExploratorySessionCard block={block} />;
    case 'rca': return <RcaBlock block={block} />;
    case 'mode_notice': return <ModeNotice block={block} />;
    case 'smoke_result': return <SmokeResult block={block} />;
    case 'run_board': return <RunBoard block={block} />;
    case 'triage_board': return <TriageBoard block={block} />;
    case 'readiness_card': return <ReadinessCard block={block} />;
    case 'share_card': return <ShareCard block={block} />;
    case 'delta_card': return <DeltaCard block={block} />;
    case 'anomaly_card': return <AnomalyCard block={block} />;
    case 'keep_green_card': return <KeepGreenCard block={block} />;
    case 'website_plan': return <WebsitePlan block={block} />;
    case 'website_result': return <WebsiteResult block={block} />;
    case 'milestone_card': return <MilestoneCard block={block} />;
    case 'environment_card': return <EnvironmentCard block={block} />;
    case 'plan_card': return <PlanCard block={block} />;
    case 'cycle_card': return <CycleCard block={block} />;
    case 'release_readiness_card': return <ReleaseReadinessCard block={block} />;
    case 'release_report': return <ReleaseReportCard block={block} />;
    case 'report_card': return <ReportCard block={block} />;
    case 'doc': return <DocBlock block={block} />;
    case 'journey_card': return <JourneyCard block={block} />;
    case 'guardrails_card': return <GuardrailsCard block={block} />;
    case 'access_card': return <AccessCard block={block} />;
    case 'build_result': return <BuildResult block={block} />;
    case 'cta': return <CtaBlock block={block} />;
    case 'export': return <ExportBlock block={block} />;
    // Metadata for callers/tests, not a card - the reply text already says it.
    case 'page_answer': return null;
    case 'error': return <ErrorBlock block={block} />;
    // A block type this bundle doesn't know how to draw is almost always one added
    // by a newer server build. Show a clear prompt to reload, never a wrong card.
    default: return (
      <div className="flex items-center gap-2 rounded-lg border border-line bg-surface-2/60 px-2.5 py-2 text-[11.5px] text-ink-3">
        <Info size={12} className="shrink-0" />
        Reload GaleQEA to view this update.
      </div>
    );
  }
}

function ExploratorySessionCard({ block }: { block: any }) {
  const running = block.status === 'running';
  return (
    <Card tone={running ? 'brand' : undefined}>
      <div className="flex items-center gap-2">
        <span className="text-[12px] font-semibold text-ink">Exploratory session</span>
        <Chip tone={running ? 'brand' : 'neutral'}>{running ? 'running' : 'ended'}</Chip>
        <span className="text-[11px] text-ink-3">
          {block.elapsed_minutes}′{block.timebox_minutes ? ` / ${block.timebox_minutes}′` : ''}
        </span>
      </div>
      <p className="mt-1 text-[11.5px] text-ink-2">{block.charter}</p>
      <div className="mt-1.5 flex gap-1.5">
        <Chip tone="neutral">{block.notes} notes</Chip>
        <Chip tone={block.bugs ? 'danger' : 'neutral'}>{block.bugs} bugs</Chip>
      </div>
      {(block.entries ?? []).length > 0 && (
        <ul className="mt-2 space-y-0.5 border-t border-line/40 pt-1.5">
          {block.entries.slice(-6).map((e: any, i: number) => (
            <li key={i} className="text-[11px] text-ink-2">
              <span className={e.kind === 'bug' ? 'text-fail' : 'text-ink-3'}>{e.kind}:</span> {e.text}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function JiraImportCard({ block }: { block: any }) {
  const chips: [string, string[], 'good' | 'brand' | 'warn'][] = [
    ['new', block.imported ?? [], 'good'],
    ['updated', block.updated ?? [], 'brand'],
    ['stale', block.stale ?? [], 'warn'],
  ];
  return (
    <Card tone="brand">
      <p className="text-[12px] font-medium text-ink">
        Imported {block.count} Jira issue(s) as requirements
      </p>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {chips.filter(([, ks]) => ks.length).map(([label, ks, tone]) => (
          <Chip key={label} tone={tone}>{ks.length} {label}</Chip>
        ))}
      </div>
      {block.jql && <p className="mono mt-1.5 text-[10.5px] text-ink-3">JQL: {block.jql}</p>}
      <p className="mt-1 text-[11px] text-ink-3">Run “generate tests” to propose tests in the review board.</p>
    </Card>
  );
}

function ApprovalPending({ block }: { block: any }) {
  return (
    <Card tone="brand">
      <div className="flex items-center gap-2 text-[12px] text-ink-2">
        <Info size={13} className="shrink-0 text-accent" />
        <span>
          Queued for approval{block.provider ? ` · ${block.provider}` : ''}. Request{' '}
          <span className="mono">{block.approval_id}</span> is waiting in the Approvals view.
          Nothing reaches the external system until a human accepts it.
        </span>
      </div>
    </Card>
  );
}

function Card({ children, tone }: { children: React.ReactNode; tone?: 'brand' | 'warn' | 'danger' }) {
  const tones = {
    brand: 'border-accent/25 bg-accent/[0.06]',
    warn: 'border-flaky/25 bg-flaky/[0.06]',
    danger: 'border-fail/25 bg-fail/[0.06]',
  };
  return (
    <div className={clsx('rounded-lg border p-2.5', tone ? tones[tone] : 'border-line bg-surface-2')}>
      {children}
    </div>
  );
}

// --------------------------------------------------------------------------- //
function RunControls({ block }: { block: ChatBlock }) {
  const { project } = useApp();
  const navigate = useNavigate();
  const [pending, setPending] = useState<string | null>(null);

  const act = async (kind: string) => {
    if (!project) return;
    setPending(kind);
    try {
      if (kind === 'cancel') {
        await api.post(`/api/projects/${project.id}/runs/${block.run_id}/cancel`);
      } else {
        const res = await api.post<any>(`/api/projects/${project.id}/runs/${block.run_id}/rerun`, {
          failed_only: kind === 'run_failed_only',
        });
        navigate(`/runs/${res.id}`);
      }
    } finally { setPending(null); }
  };

  return (
    <Card tone="brand">
      <div className="flex items-center gap-2">
        <FlaskConical size={13} className="text-accent" />
        <button
          onClick={() => navigate(`/runs/${block.run_id}`)}
          className="text-[12.5px] font-medium text-ink transition hover:text-accent"
        >
          Run #{block.number}
        </button>
        <span className="text-[11px] text-ink-3">{block.test_count} test(s)</span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <Button size="sm" variant="ghost" onClick={() => act('run_again')} disabled={pending !== null}>
          <RotateCw size={11} /> Run again
        </Button>
        <Button size="sm" variant="ghost" onClick={() => act('run_failed_only')} disabled={pending !== null}>
          <Target size={11} /> Only failed
        </Button>
        <Button size="sm" variant="ghost" onClick={() => act('cancel')} disabled={pending !== null}>
          <Ban size={11} /> Cancel
        </Button>
        <Button size="sm" variant="subtle" onClick={() => navigate(`/runs/${block.run_id}`)}>
          Open live view
        </Button>
      </div>
    </Card>
  );
}

function RunSummaryBlock({ block }: { block: ChatBlock }) {
  const navigate = useNavigate();
  const run = block.run ?? {};
  const results: any[] = block.results ?? [];
  const failed = results.filter((r) => ['failed', 'error'].includes(r.status));
  return (
    <Card>
      <div className="flex items-center gap-2">
        <StatusPill status={run.status} />
        <button onClick={() => navigate(`/runs/${run.id}`)} className="text-[12.5px] font-medium hover:text-accent">
          Run #{run.number}
        </button>
        <span className="ml-auto text-[11px] text-ink-3">{duration(run.duration_ms)}</span>
      </div>
      {failed.length > 0 && (
        <ul className="mt-2 space-y-1">
          {failed.slice(0, 5).map((r) => (
            <li key={r.id} className="flex items-baseline gap-2 text-[11.5px]">
              <span className="mono shrink-0 text-fail">{r.key}</span>
              <span className="truncate text-ink-3">{r.error || r.title}</span>
              {r.classification && <Chip tone={r.classification === 'new' ? 'danger' : 'neutral'}>{r.classification}</Chip>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function TestTable({ block }: { block: ChatBlock }) {
  const tests: any[] = block.tests ?? [];
  return (
    <Card>
      <div className="max-h-64 overflow-y-auto">
        {tests.map((t) => (
          <div key={t.id ?? t.key} className="flex items-baseline gap-2 border-b border-line/60 py-1.5 last:border-0">
            <span className="mono w-24 shrink-0 truncate text-ink-3">{t.key}</span>
            <span className="min-w-0 flex-1 truncate text-[12px] text-ink-2">{t.title}</span>
            <Chip tone={t.category === 'automated' ? 'brand' : 'neutral'}>{t.category}</Chip>
            {t.flake_score > 0.3 && <Chip tone="warn">flaky {t.flake_score}</Chip>}
          </div>
        ))}
      </div>
    </Card>
  );
}

function CoverageBlock({ block }: { block: ChatBlock }) {
  const navigate = useNavigate();
  const gaps: any[] = block.uncovered ?? [];
  const critical = gaps.filter((g) => ['critical', 'high'].includes(g.risk));
  return (
    <Card tone={critical.length ? 'warn' : undefined}>
      <div className="flex items-baseline justify-between">
        <span className="text-[12px] font-medium text-ink">Requirement coverage</span>
        <span className="mono text-ink-2">{block.coverage_pct}%</span>
      </div>
      <Meter value={block.coverage_pct} tone={critical.length ? 'flaky' : 'pass'} className="mt-2" />
      <div className="mt-2 flex gap-3 text-[11px] text-ink-3">
        <span>{block.covered_requirements}/{block.total_requirements} covered</span>
        <span>{block.automated_requirements} automated</span>
      </div>
      {critical.length > 0 && (
        <div className="mt-2 space-y-1 border-t border-line/60 pt-2">
          <p className="text-[11px] font-medium text-flaky">Untested, high risk</p>
          {critical.slice(0, 4).map((g) => (
            <div key={g.ref} className="flex items-baseline gap-2 text-[11.5px]">
              <span className="mono shrink-0 text-flaky">{g.ref}</span>
              <span className="truncate text-ink-3">{g.title}</span>
            </div>
          ))}
        </div>
      )}
      <Button size="sm" variant="subtle" className="mt-2" onClick={() => navigate('/requirements')}>
        Open traceability
      </Button>
    </Card>
  );
}

function FlakyBlock({ block }: { block: ChatBlock }) {
  const rows: any[] = block.flaky ?? [];
  return (
    <Card tone={rows.length ? 'warn' : undefined}>
      {rows.length === 0 && <p className="text-[12px] text-ink-3">No instability detected.</p>}
      {rows.slice(0, 6).map((r) => (
        <div key={r.key} className="border-b border-line/60 py-1.5 last:border-0">
          <div className="flex items-baseline gap-2">
            <span className="mono shrink-0 text-ink-3">{r.key}</span>
            <span className="min-w-0 flex-1 truncate text-[12px] text-ink-2">{r.title}</span>
            <span className="mono shrink-0 text-flaky">{(r.score * 100).toFixed(0)}%</span>
          </div>
          {r.reasons?.[0] && <p className="mt-0.5 text-[11px] text-ink-3">{r.reasons[0]}</p>}
        </div>
      ))}
    </Card>
  );
}

function SelectionBlock({ block }: { block: ChatBlock }) {
  const selected: any[] = block.selected ?? [];
  const omitted: any[] = block.omitted ?? [];
  const [showOmitted, setShowOmitted] = useState(false);
  return (
    <Card>
      <p className="text-[12px] text-ink-2">{block.coverage_note}</p>
      <div className="mt-2 space-y-1">
        {selected.slice(0, 6).map((s) => (
          <div key={s.test_case_id} className="flex items-baseline gap-2 text-[11.5px]">
            <span className="mono w-9 shrink-0 text-accent">{s.score.toFixed(2)}</span>
            <span className="mono shrink-0 text-ink-3">{s.key}</span>
            <span className="truncate text-ink-3">{s.reasons?.[0] ?? s.title}</span>
          </div>
        ))}
      </div>
      {omitted.length > 0 && (
        <button
          onClick={() => setShowOmitted((v) => !v)}
          className="mt-2 flex items-center gap-1 text-[11px] text-ink-3 transition hover:text-ink-2"
        >
          <ChevronDown size={11} className={clsx('transition', showOmitted && 'rotate-180')} />
          {omitted.length} omitted, shown in full, never silently dropped
        </button>
      )}
      {showOmitted && (
        <div className="mt-1 max-h-32 space-y-0.5 overflow-y-auto">
          {omitted.map((o) => (
            <div key={o.test_case_id} className="mono text-[10.5px] text-ink-3">{o.key} · {o.score.toFixed(2)}</div>
          ))}
        </div>
      )}
    </Card>
  );
}

function ApprovalPrompt({ block }: { block: ChatBlock }) {
  const { project, refreshOverview } = useApp();
  const [state, setState] = useState<'pending' | 'approved' | 'rejected'>('pending');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const decide = async (decision: 'approve' | 'reject') => {
    if (!project) return;
    setBusy(true); setError('');
    try {
      await api.post(`/api/projects/${project.id}/approvals/${block.approval_id}/decide`, { decision });
      setState(decision === 'approve' ? 'approved' : 'rejected');
      void refreshOverview();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'could not record that decision');
    } finally { setBusy(false); }
  };

  if (state !== 'pending') {
    return (
      <Card>
        <div className={clsx('flex items-center gap-2 text-[12px]', state === 'approved' ? 'text-pass' : 'text-ink-3')}>
          {state === 'approved' ? <Check size={13} /> : <X size={13} />}
          {state === 'approved' ? 'Approved and applied.' : 'Rejected. Nothing changed.'}
        </div>
      </Card>
    );
  }

  return (
    <Card tone="brand">
      <div className="flex items-center gap-2">
        <ShieldAlert size={13} className="text-accent" />
        <span className="text-[12px] font-medium text-ink">Approval required</span>
        <span className={clsx('ml-auto rounded-md border px-1.5 py-px text-[10px]', RISK_COLOR[block.risk] ?? RISK_COLOR.medium)}>
          {block.risk} risk
        </span>
      </div>
      <p className="mt-1.5 text-[11.5px] leading-relaxed text-ink-3">
        The assistant proposed <code className="mono text-ink-2">{block.tool}</code>. It has not run.
        Nothing changes until you approve it.
      </p>
      {block.arguments && (
        <pre tabIndex={0} className="rounded-md mono mt-2 max-h-28 overflow-auto bg-canvas/60 p-2 text-[10.5px] text-ink-3">
          {JSON.stringify(block.arguments, null, 2)}
        </pre>
      )}
      {error && <p className="mt-1.5 text-[11px] text-fail">{error}</p>}
      <div className="mt-2 flex gap-1.5">
        <Button size="sm" variant="primary" onClick={() => decide('approve')} disabled={busy}>
          <Check size={11} /> Approve
        </Button>
        <Button size="sm" variant="ghost" onClick={() => decide('reject')} disabled={busy}>
          <X size={11} /> Reject
        </Button>
      </div>
    </Card>
  );
}

function ApprovalList({ block }: { block: ChatBlock }) {
  const navigate = useNavigate();
  const items: any[] = block.items ?? [];
  return (
    <Card tone="brand">
      <div className="space-y-1.5">
        {items.slice(0, 6).map((item) => (
          <div key={item.id} className="flex items-baseline gap-2 text-[11.5px]">
            <span className={clsx('shrink-0 rounded-md border px-1 text-[10px]', RISK_COLOR[item.risk] ?? RISK_COLOR.medium)}>
              {item.risk}
            </span>
            <span className="truncate text-ink-2">{item.title}</span>
          </div>
        ))}
      </div>
      <Button size="sm" variant="ghost" className="mt-2" onClick={() => navigate('/approvals')}>
        Review all {items.length}
      </Button>
    </Card>
  );
}

function RcaBlock({ block }: { block: ChatBlock }) {
  const [open, setOpen] = useState(false);
  const hypotheses: any[] = block.hypotheses ?? [];
  const evidence: any[] = block.evidence ?? [];
  const top = hypotheses[0];
  return (
    <Card tone="danger">
      <div className="flex items-center gap-2">
        <Sparkles size={13} className="text-fail" />
        <span className="text-[12px] font-medium text-ink">Root cause</span>
        <Chip tone="neutral" className="ml-auto">{block.category}</Chip>
        <span className="mono text-[10.5px] text-ink-3">{Math.round((block.confidence ?? 0) * 100)}%</span>
      </div>
      {top && (
        <>
          <p className="mt-1.5 text-[12px] leading-relaxed text-ink-2">{top.cause}</p>
          <p className="mt-1.5 text-[11.5px] leading-relaxed text-accent">→ {top.next_step}</p>
        </>
      )}
      <button
        onClick={() => setOpen((v) => !v)}
        className="mt-2 flex items-center gap-1 text-[11px] text-ink-3 transition hover:text-ink-2"
      >
        <ChevronDown size={11} className={clsx('transition', open && 'rotate-180')} />
        {hypotheses.length} hypotheses · {evidence.length} pieces of evidence
      </button>
      {open && (
        <div className="mt-2 space-y-2 border-t border-line/60 pt-2">
          {hypotheses.map((h, i) => (
            <div key={i} className="space-y-0.5">
              <div className="flex items-baseline gap-2">
                <span className="mono text-[10px] text-ink-3">{Math.round(h.confidence * 100)}%</span>
                <span className="text-[11.5px] text-ink-2">{h.cause}</span>
              </div>
              <div className="flex flex-wrap gap-1 pl-8">
                {(h.cites ?? []).map((c: string) => (
                  <span key={c} className="rounded-md mono bg-surface-3 px-1 text-[9.5px] text-ink-3">{c}</span>
                ))}
              </div>
            </div>
          ))}
          <div className="space-y-1 border-t border-line/60 pt-2">
            {evidence.map((e) => (
              <div key={e.id} className="flex items-baseline gap-2 text-[11px]">
                <span className="mono shrink-0 text-accent">{e.id}</span>
                <span className="text-ink-3">{e.summary}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function ModeNotice({ block }: { block: ChatBlock }) {
  return (
    <Card>
      <div className="flex items-center gap-2">
        <Info size={13} className="text-ink-3" />
        <span className="text-[12px] font-medium text-ink-2">Runs without a model. Connect one for more</span>
      </div>
      <ul className="mt-1.5 space-y-0.5">
        {(block.capabilities ?? []).map((c: string) => (
          <li key={c} className="flex items-baseline gap-1.5 text-[11.5px] text-ink-3">
            <Check size={10} className="shrink-0 text-pass" /> {c}
          </li>
        ))}
      </ul>
    </Card>
  );
}

interface TestType {
  key: string; label: string; category: string; count: number; risk: string;
  confidence: string; est_minutes: number; est_tokens: number; enabled: boolean; why: string;
}

/** The plan's test types as a toggleable review board: every kind of test with
 *  its count, risk, confidence, run-minutes and build tokens (0 to re-run).
 *  Clicking a row toggles it in chat, producing a new plan version. */
function TestTypesTable({ types, totals, version }: { types: TestType[]; totals: Record<string, number>; version: number }) {
  const groups: [string, string][] = [
    ['functional', 'Functional'], ['non_functional', 'Non-functional'], ['manual', 'Manual'],
  ];
  const riskTone = (r: string) => (r === 'critical' || r === 'high' ? 'danger' : r === 'medium' ? 'warn' : 'neutral');
  return (
    <div className="mt-2.5">
      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-1 text-[10px] text-ink-3">
        <span>Plan v{version} · {totals.types_enabled}/{totals.types_total} types · {totals.test_count} tests</span>
        <span>~{totals.est_minutes} min · ~{(totals.est_build_tokens ?? 0).toLocaleString()} build tok · 0 on re-run</span>
      </div>
      {groups.map(([cat, label]) => {
        const rows = types.filter((t) => t.category === cat);
        if (!rows.length) return null;
        return (
          <div key={cat} className="mb-1.5">
            <p className="text-[9.5px] font-semibold uppercase tracking-wide text-ink-3">{label}</p>
            <ul className="mt-0.5 space-y-0.5">
              {rows.map((t) => (
                <li key={t.key}>
                  <button
                    onClick={() => runCommand(`${t.enabled ? 'disable' : 'enable'} ${t.label}`)}
                    title={t.why}
                    className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-[11px] transition hover:bg-surface-2"
                  >
                    <span className={clsx('flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm border',
                      t.enabled ? 'border-accent bg-accent text-canvas' : 'border-line')}>
                      {t.enabled && <Check size={9} />}
                    </span>
                    <span className={clsx('min-w-0 flex-1 truncate', t.enabled ? 'text-ink' : 'text-ink-3')}>{t.label}</span>
                    <span className="mono shrink-0 text-[9.5px] text-ink-3">{t.count}×</span>
                    <Chip tone={riskTone(t.risk)}>{t.risk}</Chip>
                    <span className="shrink-0 text-[9.5px] text-ink-3" title="confidence">{t.confidence}</span>
                    <span className="mono shrink-0 text-[9.5px] text-ink-3">{t.est_minutes}m</span>
                    <span className="mono shrink-0 text-[9.5px] text-ink-3">{t.est_tokens ? `${Math.round(t.est_tokens / 1000)}k` : '0'}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

function WebsitePlan({ block }: { block: ChatBlock }) {
  const [showPages, setShowPages] = useState(false);
  const pages: string[] = block.pages ?? [];
  const functional: string[] = block.functional ?? [];
  const nonFunctional: string[] = block.non_functional ?? [];
  const notes: string[] = block.notes ?? [];
  const testTypes = (block.test_types ?? []) as TestType[];
  const totals = (block.totals ?? {}) as Record<string, number>;
  const pagePath = (p: string) => { try { return new URL(p).pathname || '/'; } catch { return p; } };
  return (
    <Card tone="brand">
      <div className="flex items-center gap-2">
        <Compass size={13} className="text-accent" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">Test plan · {block.target}</span>
        {block.enriched
          ? <Chip tone="brand">AI-planned</Chip>
          : <Chip tone="neutral">{block.test_count} pages</Chip>}
      </div>

      <button
        onClick={() => setShowPages((v) => !v)}
        className="mt-2 flex items-center gap-1 text-[11px] text-ink-3 transition hover:text-ink-2"
      >
        <ChevronDown size={11} className={clsx('transition', showPages && 'rotate-180')} />
        {block.test_count} page(s) discovered
      </button>
      {showPages && (
        <ul className="mt-1 max-h-32 space-y-0.5 overflow-y-auto border-l border-line/60 pl-2">
          {pages.map((p) => (
            <li key={p} className="mono truncate text-[10.5px] text-ink-3">{pagePath(p)}</li>
          ))}
        </ul>
      )}

      {testTypes.length > 0 ? (
        <TestTypesTable types={testTypes} totals={totals} version={block.plan_version as number} />
      ) : (
        <div className="mt-2.5 grid gap-2.5">
          <div>
            <p className="text-[10.5px] font-semibold uppercase tracking-wide text-ink-3">Functional</p>
            <ul className="mt-1 space-y-0.5">
              {functional.map((f) => (
                <li key={f} className="flex items-baseline gap-1.5 text-[11.5px] text-ink-2">
                  <Check size={10} className="shrink-0 text-pass" /> {f}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="text-[10.5px] font-semibold uppercase tracking-wide text-ink-3">Non-functional</p>
            <ul className="mt-1 space-y-0.5">
              {nonFunctional.map((f) => (
                <li key={f} className="flex items-baseline gap-1.5 text-[11.5px] text-ink-2">
                  <Check size={10} className="shrink-0 text-pass" /> {f}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {notes.length > 0 && (
        <ul className="mt-2.5 space-y-1 rounded-md border border-flaky/25 bg-flaky/[0.07] px-2 py-1.5">
          {notes.map((n) => (
            <li key={n} className="flex items-baseline gap-1.5 text-[10.5px] leading-relaxed text-flaky">
              <AlertTriangle size={10} className="mt-px shrink-0" /> {n}
            </li>
          ))}
        </ul>
      )}

      {(block.auth_gated as number) > 0 && (
        <button
          onClick={() => runCommand('add test credentials')}
          className="mt-2 flex w-full items-center gap-1.5 rounded-md border border-accent/30 bg-accent/[0.06] px-2 py-1.5 text-left text-[11px] text-accent transition hover:bg-accent/10"
        >
          <Lock size={11} className="shrink-0" />
          {block.auth_gated as number} page(s) need a login. Set up access to cover them
        </button>
      )}

      {(block.discovered as number) > (block.test_count as number) && (
        <div className="mt-2 flex items-center gap-2 text-[11px] text-ink-3">
          <span>Testing {block.test_count as number} of {block.discovered as number} pages found.</span>
          <button onClick={() => runCommand('test all pages')} className="font-medium text-accent hover:underline">
            Test all {block.discovered as number}
          </button>
        </div>
      )}

      <div className="mt-3 flex gap-1.5">
        <Button size="sm" variant="primary" onClick={() => runCommand('approve')}>Approve &amp; run</Button>
        <Button size="sm" variant="subtle" onClick={() => runCommand('cancel')}>Cancel</Button>
      </div>
    </Card>
  );
}

/** The Golden Path stage card: where a target is on the rail, and the one button
 *  that moves it forward, with the plain-English command shown, so the chat stays
 *  a replayable log. Every stage renders through this, never a dead end. */
function JourneyCard({ block }: { block: ChatBlock }) {
  const rail = (block.rail ?? []) as { stage: string; done: boolean; current: boolean }[];
  const next = block.next as { command: string; label: string } | undefined;
  return (
    <Card tone="brand">
      <div className="flex items-center gap-2">
        <Compass size={13} className="text-accent" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">{block.target}</span>
        <Chip tone="neutral">{block.environment}</Chip>
      </div>
      <div className="mt-2 rounded-md border border-line bg-surface-2/60 px-2 py-1.5">
        <JourneyRail rail={rail} />
      </div>
      <p className="mt-2 text-[11.5px] text-ink-2">
        Stage: <span className="font-medium text-ink">{stageLabel(String(block.stage ?? ''))}</span>
      </p>
      {next && (
        <div className="mt-2.5 flex items-center gap-2">
          <Button size="sm" variant="primary" onClick={() => runCommand(next.command)}>{next.label}</Button>
          <span className="mono text-[10.5px] text-ink-3">or type “{next.command}”</span>
        </div>
      )}
    </Card>
  );
}

/** The Guardrails stage is a real stop the first time a target is seen: what the
 *  agent will and won't do, defaulting to safe (read-only on anything that looks
 *  like production). Editable in one click; remembered per target. */
function GuardrailsCard({ block }: { block: ChatBlock }) {
  const g = (block.guardrails ?? {}) as Record<string, any>;
  const rail = (block.rail ?? []) as { stage: string; done: boolean; current: boolean; skipped?: boolean }[];
  const readOnly = !!g.read_only;
  return (
    <Card tone="brand">
      <div className="flex items-center gap-2">
        <ShieldAlert size={13} className="text-accent" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">Guardrails · {block.target}</span>
        <Chip tone="neutral">{block.environment}</Chip>
      </div>
      {rail.length > 0 && (
        <div className="mt-2 rounded-md border border-line bg-surface-2/60 px-2 py-1.5"><JourneyRail rail={rail} /></div>
      )}
      <ul className="mt-2.5 space-y-1 text-[11.5px] text-ink-2">
        <li className="flex items-center gap-1.5">
          {readOnly ? <ShieldCheck size={11} className="text-pass" /> : <AlertTriangle size={11} className="text-flaky" />}
          <span className="font-medium">{readOnly ? 'Read-only' : 'Writes allowed'}</span>: {g.reason}
        </li>
        <li>Test data: <span className="font-medium">{g.data_policy}</span></li>
        <li>Never touch: {(g.avoid ?? []).join(', ')}</li>
        <li>Rate limit: {g.rate_limit_rps}/s</li>
      </ul>
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        <Button size="sm" variant="primary" onClick={() => runCommand('continue')}>Continue</Button>
        <Button size="sm" variant="subtle" onClick={() => runCommand(readOnly ? 'allow writes' : 'make it read-only')}>
          {readOnly ? 'Allow writes' : 'Make read-only'}
        </Button>
        <span className="mono text-[10.5px] text-ink-3">remembered for {block.target}</span>
      </div>
    </Card>
  );
}

/** The Build stage: the plan filed as durable, reviewable tests-as-data with
 *  provenance back to the plan. Not a throwaway run config: tests you own. */
function BuildResult({ block }: { block: ChatBlock }) {
  const navigate = useNavigate();
  const suites = (block.suites ?? []) as { key: string; name: string; count: number; risk?: string }[];
  const tests = (block.tests ?? []) as { key: string; type: string; label: string; unit?: string; smoke?: boolean }[];
  const smoke = (block.smoke_count as number) ?? 0;
  return (
    <Card tone="brand">
      <div className="flex items-center gap-2">
        <FlaskConical size={13} className="text-accent" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">
          Built {block.filed as number} test(s) · {suites.length} suite(s) · {block.target}
        </span>
      </div>
      {/* Per-type suites, one test per page / form / endpoint, so failures read
          "a11y: /login" not "accessibility failed". */}
      <ul className="mt-2 space-y-0.5 border-l border-line/60 pl-2">
        {suites.map((s) => (
          <li key={s.key} className="flex items-baseline gap-2 text-[11px]">
            <span className="min-w-0 flex-1 truncate text-ink-2">{s.name.replace('Golden Path · ', '')}</span>
            {s.risk && <Chip tone={s.risk === 'high' ? 'danger' : 'neutral'}>{s.risk}</Chip>}
            <span className="mono shrink-0 tabular-nums text-ink-3">{s.count}×</span>
          </li>
        ))}
      </ul>
      {smoke > 0 && (
        <p className="mt-2 text-[11px] text-ink-3">
          {smoke} tagged <span className="mono text-ink-2">smoke</span>. A ≤3-min front-door check runs first.
        </p>
      )}
      {tests.length > 0 && (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-[11px] text-ink-3 hover:text-ink-2">
            All {tests.length} test(s)
          </summary>
          <ul className="mt-1 max-h-40 space-y-0.5 overflow-y-auto border-l border-line/60 pl-2">
            {tests.map((t) => (
              <li key={t.key} className="flex items-baseline gap-2 text-[11px]">
                <span className="mono shrink-0 text-ink-3">{t.key}</span>
                <span className="min-w-0 flex-1 truncate text-ink-2">{t.label}</span>
                {t.smoke && <Chip tone="brand">smoke</Chip>}
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        <Button size="sm" variant="primary" onClick={() => runCommand('smoke it')}>Smoke-check first</Button>
        <Button size="sm" variant="subtle" onClick={() => runCommand('approve')}>Approve &amp; run all</Button>
        <Button size="sm" variant="ghost" onClick={() => navigate('/tests')}>Open in Tests</Button>
      </div>
    </Card>
  );
}

/** The Access stage, surfaced when discovery hits a login wall. Three ways
 *  forward, never a dead end: log in for me, add test credentials, or skip. */
function AccessCard({ block }: { block: ChatBlock }) {
  const gated = (block.gated ?? []) as string[];
  const pagePath = (p: string) => { try { return new URL(p).pathname; } catch { return p; } };
  return (
    <Card tone="brand">
      <div className="flex items-center gap-2">
        <Lock size={13} className="text-accent" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">Access · {block.target}</span>
      </div>
      <p className="mt-1.5 text-[11.5px] text-ink-2">
        {block.auth_count as number} page(s) are behind a login and were left out.
      </p>
      {Array.isArray(block.auth_kinds) && (block.auth_kinds as string[]).length > 0 && (
        <div className="mt-1 flex flex-wrap items-center gap-1">
          <span className="text-[10.5px] text-ink-3">detected:</span>
          {(block.auth_kinds as string[]).map((k) => <Chip key={k} tone="neutral">{k} auth</Chip>)}
        </div>
      )}
      {gated.length > 0 && (
        <ul className="mt-1 space-y-0.5 border-l border-line/60 pl-2">
          {gated.map((p) => <li key={p} className="mono truncate text-[10.5px] text-ink-3">{pagePath(p)}</li>)}
        </ul>
      )}
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        <Button size="sm" variant="primary" onClick={() => runCommand('log in for me')}>Log in for me</Button>
        <Button size="sm" variant="subtle" onClick={() => runCommand('add test credentials')}>Add credentials</Button>
        <Button size="sm" variant="ghost" onClick={() => runCommand('skip gated pages')}>Skip gated pages</Button>
      </div>
    </Card>
  );
}

/** A report offered in chat, with the same Export ▾ / Copy-for-AI actions the
 *  report pages carry. The chat is the primary interface; a report is a card. */
function ReportCard({ block }: { block: ChatBlock }) {
  const name = String(block.report ?? 'report');
  return (
    <Card tone="brand">
      <div className="flex items-center gap-2">
        <FileText size={13} className="text-accent" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">{block.title ?? 'Report'}</span>
      </div>
      {block.summary && <p className="mt-1.5 text-[11.5px] leading-relaxed text-ink-2">{block.summary}</p>}
      <div className="mt-2.5">
        <ReportExport base={String(block.api_base ?? '')} name={name} junit={!!block.junit} />
      </div>
    </Card>
  );
}

/** Markdown rendered inline: planning-tool output (coverage plan, status brief,
 *  quality retro) as a readable card rather than pasted JSON. */
function DocBlock({ block }: { block: ChatBlock }) {
  return (
    <Card>
      {block.title && (
        <p className="mb-1.5 text-[10.5px] font-semibold uppercase tracking-wide text-ink-3">{block.title}</p>
      )}
      <div
        className="prose-doc max-w-none text-[12px]"
        dangerouslySetInnerHTML={{ __html: renderMarkdown(String(block.markdown ?? '')) }}
      />
    </Card>
  );
}

function WebsiteResult({ block }: { block: ChatBlock }) {
  const navigate = useNavigate();
  const ok = !!block.ok;
  const pages: { title: string; status: string }[] = block.pages ?? [];
  const total = (block.passed ?? 0) + (block.failed ?? 0);
  return (
    <Card tone={ok ? undefined : 'danger'}>
      <div className="flex items-center gap-2">
        {ok ? <Check size={13} className="text-pass" /> : <X size={13} className="text-fail" />}
        <span className="text-[12px] font-medium text-ink-2">
          Tested {total} page{total === 1 ? '' : 's'}: {block.passed ?? 0} passed{block.failed ? `, ${block.failed} failed` : ''}
        </span>
        {block.run_number ? <span className="ml-auto text-[11px] text-ink-3">run #{block.run_number}</span> : null}
      </div>
      <div className="mono mt-0.5 truncate text-[10.5px] text-ink-3">{block.target}</div>
      {pages.length > 0 && (
        <ul className="mt-2 max-h-44 space-y-1 overflow-y-auto">
          {pages.map((p) => (
            <li key={p.title} className="flex items-center gap-2 text-[11px]">
              <StatusPill status={p.status} />
              <span className="min-w-0 flex-1 truncate text-ink-3">{p.title.replace('Page loads: ', '')}</span>
            </li>
          ))}
        </ul>
      )}
      {block.run_id && (
        <div className="mt-2.5">
          <Button size="sm" variant="ghost" onClick={() => navigate(`/runs/${block.run_id}`)}>View report</Button>
        </div>
      )}
    </Card>
  );
}

function SmokeResult({ block }: { block: ChatBlock }) {
  const navigate = useNavigate();
  const ok = !!block.ok;
  const timedOut = !!block.timed_out;
  const blockers = (block.blockers ?? []) as { title: string; check: string; detail: string }[];
  const consoleErrors = (block.console_errors ?? []).length;   // legacy smoke-probe shape
  const networkFailures = (block.network_failures ?? []).length;
  const total = block.total as number | undefined;
  return (
    <Card tone={ok ? undefined : timedOut ? 'warn' : 'danger'}>
      <div className="flex items-center gap-2">
        {ok ? <Check size={13} className="text-pass" />
            : timedOut ? <Clock size={13} className="text-warn" />
            : <X size={13} className="text-fail" />}
        <span className="text-[12px] font-medium text-ink-2">
          {ok ? 'Smoke passed. Full suite clear' : timedOut ? 'Smoke still running' : 'Smoke found blockers'}
        </span>
        {block.run_number ? (
          <span className="ml-auto mono text-[11px] text-ink-3">run #{block.run_number}</span>
        ) : null}
      </div>
      <div className="mt-1 flex items-baseline gap-1.5 text-[11.5px] text-ink-3">
        <Target size={10} className="shrink-0" /> {block.target}
        {total != null && (
          <span className="ml-auto tabular-nums">{block.passed as number}/{total} passed</span>
        )}
      </div>
      {blockers.length > 0 && (
        <ul className="mt-2 space-y-1 border-l-2 border-fail/50 pl-2">
          {blockers.map((b, i) => (
            <li key={i} className="text-[11px]">
              <div className="flex items-baseline gap-1.5">
                <Chip tone="danger">{b.check}</Chip>
                <span className="min-w-0 flex-1 truncate text-ink-2">{b.title}</span>
              </div>
              <p className="mt-0.5 truncate text-ink-3">{b.detail}</p>
            </li>
          ))}
        </ul>
      )}
      {(consoleErrors > 0 || networkFailures > 0) && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {consoleErrors > 0 && <Chip tone="warn">{consoleErrors} console error{consoleErrors > 1 ? 's' : ''}</Chip>}
          {networkFailures > 0 && <Chip tone="warn">{networkFailures} server (5xx)</Chip>}
        </div>
      )}
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {ok
          ? <Button size="sm" variant="primary" onClick={() => runCommand('run the full suite')}>Run the full suite</Button>
          : !timedOut && <Button size="sm" variant="subtle" onClick={() => runCommand('run the full suite')}>Run anyway</Button>}
        {block.run_id && (
          <Button size="sm" variant="ghost" onClick={() => navigate('/runs')}>View run</Button>
        )}
      </div>
    </Card>
  );
}

/** The Run board: the live face of a Golden Path run. Totals, ETA, running cost
 *  (0, because execution needs no model), and per-type progress bars. */
function RunBoard({ block }: { block: ChatBlock }) {
  const navigate = useNavigate();
  const t = (block.totals ?? {}) as Record<string, number>;
  const byType = (block.by_type ?? []) as { type: string; label: string; total: number; passed: number; failed: number; running: number }[];
  const done = !!block.done;
  const eta = (block.eta_seconds as number) ?? 0;
  const notRun = (block.not_run ?? []) as { key: string; label: string; reason: string }[];
  return (
    <Card tone={block.has_failures ? 'danger' : 'brand'}>
      <div className="flex items-center gap-2">
        <PlayCircle size={13} className="text-accent" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">
          {done ? 'Run complete' : 'Running'} · {block.target}
        </span>
        <span className="mono shrink-0 text-[11px] text-ink-3">run #{block.run_number as number}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Chip tone="good">{t.passed ?? 0} passed</Chip>
        {(t.failed ?? 0) > 0 && <Chip tone="danger">{t.failed} failed</Chip>}
        {(t.running ?? 0) > 0 && <Chip tone="brand">{t.running} running</Chip>}
        {(t.queued ?? 0) > 0 && <Chip tone="neutral">{t.queued} queued</Chip>}
        {(t.blocked ?? 0) > 0 && <Chip tone="warn">{t.blocked} blocked</Chip>}
        <span className="ml-auto flex items-center gap-2 text-[11px] text-ink-3">
          {!done && eta > 0 && <span>ETA ~{eta < 60 ? `${eta}s` : `${Math.round(eta / 60)}m`}</span>}
          <span className="mono">$0 · 0 tokens</span>
        </span>
      </div>
      <ul className="mt-2 space-y-1">
        {byType.map((g) => {
          const complete = g.passed + g.failed;
          const pct = g.total ? Math.round((complete / g.total) * 100) : 0;
          return (
            <li key={g.type} className="text-[11px]">
              <div className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1 truncate text-ink-2">{g.label}</span>
                {g.failed > 0 && <span className="text-fail">{g.failed}✗</span>}
                <span className="mono tabular-nums text-ink-3">{complete}/{g.total}</span>
              </div>
              <div className="mt-0.5 h-1 overflow-hidden rounded-full bg-surface-2">
                <div className={`h-full ${g.failed > 0 ? 'bg-fail' : 'bg-pass'}`} style={{ width: `${pct}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
      {notRun.length > 0 && (
        <ul className="mt-2 space-y-0.5 border-l-2 border-warn/40 pl-2">
          {notRun.map((n) => (
            <li key={n.key} className="flex items-baseline gap-2 text-[11px]">
              <span className="min-w-0 flex-1 truncate text-ink-3">{n.label}</span>
              <Chip tone="warn">skipped: {n.reason}</Chip>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {block.has_failures ? (
          <Button size="sm" variant="primary" onClick={() => runCommand('triage')}>Triage failures</Button>
        ) : done ? (
          <Button size="sm" variant="primary" onClick={() => runCommand("what's next")}>What&apos;s next</Button>
        ) : null}
        <Button size="sm" variant="ghost" onClick={() => navigate(`/runs/${block.run_id}`)}>Watch live</Button>
      </div>
      <ManualChecklist runId={String(block.run_id)} rows={(block.manual ?? []) as ManualRow[]} />
    </Card>
  );
}

type ManualRow = { id: string; key: string; title: string; status: string; pending: boolean; note: string; executed_by: string };

/** The human checklist that rides a run: manual + exploratory-charter rows a
 *  tester marks pass/fail/blocked/skipped. Hotkeys: j/k move, p/f/b/s mark. */
function ManualChecklist({ runId, rows }: { runId: string; rows: ManualRow[] }) {
  const { project } = useApp();
  const [state, setState] = useState<Record<string, { status: string; by: string }>>({});
  const [sel, setSel] = useState(0);
  const MARKS: Record<string, string> = { p: 'passed', f: 'failed', b: 'blocked', s: 'skipped' };
  if (!rows.length) return null;
  const rowStatus = (r: ManualRow) => state[r.id]?.status ?? (r.pending ? 'pending' : r.status);
  const tone = (s: string) => (s === 'passed' ? 'good' : s === 'failed' || s === 'blocked' ? 'danger' : s === 'skipped' ? 'neutral' : 'warn');

  const mark = async (r: ManualRow, status: string) => {
    if (!project) return;
    setState((s) => ({ ...s, [r.id]: { status, by: 'you' } }));
    try {
      await api.post(`/api/projects/${project.id}/runs/${runId}/manual/${r.id}`, { status });
    } catch { setState((s) => ({ ...s, [r.id]: { status: 'pending', by: '' } })); }
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'j') setSel((i) => Math.min(rows.length - 1, i + 1));
    else if (e.key === 'k') setSel((i) => Math.max(0, i - 1));
    else if (MARKS[e.key]) { void mark(rows[sel], MARKS[e.key]); }
    else return;
    e.preventDefault();
  };

  return (
    <div className="mt-2.5 border-t border-line/60 pt-2 outline-none" tabIndex={0} onKeyDown={onKey}>
      <p className="mb-1 flex items-center gap-1.5 text-[10.5px] text-ink-3">
        <ClipboardList size={11} /> Manual checklist ({rows.length}): <span className="mono">j/k</span> move, <span className="mono">p/f/b/s</span> mark
      </p>
      <ul className="space-y-1">
        {rows.map((r, i) => {
          const st = rowStatus(r);
          return (
            <li key={r.id} className={`rounded px-1.5 py-1 ${i === sel ? 'bg-surface-2' : ''}`}>
              <div className="flex items-baseline gap-2 text-[11px]">
                <Chip tone={tone(st)}>{st}</Chip>
                <span className="min-w-0 flex-1 truncate text-ink-2">{r.title}</span>
                {(state[r.id]?.by || r.executed_by) && <span className="shrink-0 text-[10px] text-ink-3">by {state[r.id]?.by || r.executed_by}</span>}
              </div>
              {st === 'pending' && (
                <div className="mt-1 flex gap-1">
                  {(['passed', 'failed', 'blocked', 'skipped'] as const).map((s) => (
                    <button key={s} onClick={() => mark(r, s)}
                      className="rounded border border-line px-1.5 py-0.5 text-[10.5px] text-ink-3 hover:border-accent hover:text-ink">
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Keep-green: the run becomes an ongoing guarantee. A schedule, a copy-paste CI
 *  workflow, and webhook routing. */
function KeepGreenCard({ block }: { block: ChatBlock }) {
  const navigate = useNavigate();
  const [copied, setCopied] = useState(false);
  const [showYaml, setShowYaml] = useState(false);
  const sched = (block.schedule ?? {}) as { human_cron?: string; cron?: string; tests?: number };
  const yaml = String(block.github_action ?? '');
  const copyYaml = async () => {
    try { await navigator.clipboard.writeText(yaml); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* no clipboard */ }
  };
  return (
    <Card tone="brand">
      <div className="flex items-center gap-2">
        <RotateCw size={13} className="text-accent" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">Keep green · {block.target}</span>
      </div>
      <ul className="mt-2 space-y-1 text-[11.5px] text-ink-2">
        <li className="flex items-baseline gap-2">
          <Calendar size={11} className="shrink-0 text-ink-3" />
          <span className="flex-1">Scheduled <span className="text-ink">{sched.human_cron}</span> · {sched.tests} test(s)</span>
        </li>
        <li className="flex items-baseline gap-2">
          <RotateCw size={11} className="shrink-0 text-ink-3" />
          <span className="flex-1">{block.routing as string}</span>
        </li>
      </ul>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <Button size="sm" variant="subtle" onClick={() => setShowYaml((v) => !v)}>{showYaml ? 'Hide' : 'GitHub Action YAML'}</Button>
        <Button size="sm" variant="ghost" onClick={copyYaml}>{copied ? 'Copied' : 'Copy YAML'}</Button>
        <Button size="sm" variant="ghost" onClick={() => navigate('/settings')}>Route to Slack</Button>
      </div>
      {showYaml && (
        <pre tabIndex={0} className="mono mt-2 max-h-56 overflow-auto rounded border border-line bg-surface-2 p-2 text-[10px] leading-relaxed text-ink-2">{yaml}</pre>
      )}
    </Card>
  );
}

/** The exploratory anomaly card: what a guardrail-bounded poke turned up. Each
 *  anomaly can become a proposed test or a filed bug. */
function AnomalyCard({ block }: { block: ChatBlock }) {
  const findings = (block.findings ?? []) as { id: string; kind: string; severity: string; title: string; detail: string; url: string; promoted_test_id: string | null }[];
  const sevTone = (s: string) => (s === 'high' ? 'danger' : s === 'medium' ? 'warn' : 'neutral');
  return (
    <Card tone={findings.some((f) => f.severity === 'high') ? 'danger' : 'brand'}>
      <div className="flex items-center gap-2">
        <Compass size={13} className="text-accent" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">
          Explored {block.area as string} · as {block.role as string} · {block.minutes as number} min
        </span>
        <span className="shrink-0 text-[11px] text-ink-3">{findings.length} anomaly(ies)</span>
      </div>
      {block.summary ? <p className="mt-1.5 text-[11.5px] text-ink-2">{block.summary as string}</p> : null}
      {findings.length === 0 ? (
        <p className="mt-2 text-[11px] text-ink-3">Nothing worth reporting. The area held up.</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {findings.map((f) => (
            <li key={f.id} className="rounded-md border border-line/60 p-2">
              <div className="flex items-baseline gap-1.5">
                <Chip tone={sevTone(f.severity)}>{f.severity}</Chip>
                <Chip tone="neutral">{f.kind}</Chip>
                <span className="min-w-0 flex-1 truncate text-[11px] text-ink-2">{f.title || f.detail}</span>
              </div>
              {f.url ? <p className="mono mt-0.5 truncate text-[10px] text-ink-3">{f.url}</p> : null}
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {f.promoted_test_id ? (
                  <Chip tone="good">→ filed as a test</Chip>
                ) : (
                  <>
                    <Button size="sm" variant="primary" onClick={() => runCommand(`make a test for ${f.id}`)}>Make this a test</Button>
                    <Button size="sm" variant="ghost" onClick={() => runCommand(`file a bug for ${f.id}`)}>File bug</Button>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** The regression delta: what changed vs the baseline run. New failures, fixes,
 *  still-failing, newly flaky. Only the changed rows are shown. */
function DeltaCard({ block }: { block: ChatBlock }) {
  const navigate = useNavigate();
  const totals = (block.totals ?? {}) as Record<string, number>;
  const rows = (block.rows ?? []) as { key: string; unit: string; type: string; current: string; baseline: string; delta: string }[];
  const hasBaseline = !!block.has_baseline;
  const LABEL: Record<string, string> = {
    new_fail: 'new fail', still_failing: 'still failing', new_flaky: 'new flaky', fixed: 'fixed',
  };
  const TONE: Record<string, 'danger' | 'warn' | 'good' | 'neutral'> = {
    new_fail: 'danger', still_failing: 'warn', new_flaky: 'warn', fixed: 'good',
  };
  const worst = block.rows && (totals.new_fail || totals.still_failing);
  return (
    <Card tone={worst ? 'danger' : 'brand'}>
      <div className="flex items-center gap-2">
        <GitCompare size={13} className={worst ? 'text-fail' : 'text-accent'} />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">
          {hasBaseline ? `Since run #${block.baseline_run_number}` : 'Regression delta'}
        </span>
        <span className="mono shrink-0 text-[11px] text-ink-3">run #{block.run_number as number}</span>
      </div>
      <p className="mt-1.5 text-[11.5px] text-ink-2">{block.what_changed as string}</p>
      {hasBaseline && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {(['new_fail', 'fixed', 'still_failing', 'new_flaky'] as const).map((k) => (
            (totals[k] ?? 0) > 0 ? <Chip key={k} tone={TONE[k]}>{totals[k]} {LABEL[k]}</Chip> : null
          ))}
          {block.unchanged ? <Chip tone="neutral">{block.unchanged as number} unchanged</Chip> : null}
        </div>
      )}
      {rows.length > 0 && (
        <ul className="mt-2 max-h-48 space-y-0.5 overflow-y-auto border-l border-line/60 pl-2">
          {rows.map((r) => (
            <li key={r.key} className="flex items-baseline gap-2 text-[11px]">
              <Chip tone={TONE[r.delta] ?? 'neutral'}>{LABEL[r.delta] ?? r.delta}</Chip>
              <span className="min-w-0 flex-1 truncate text-ink-2">{r.unit || r.key}</span>
              <span className="mono shrink-0 text-[10px] text-ink-3">{r.baseline}→{r.current}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        <Button size="sm" variant="ghost" onClick={() => navigate(`/runs/${block.run_id}`)}>Open run</Button>
        {worst ? <Button size="sm" variant="primary" onClick={() => runCommand('triage')}>Triage</Button> : null}
      </div>
    </Card>
  );
}

/** The Share card: the release report published in every format through the
 *  storage interface. No format is a dead end; team integrations lead to Settings. */
function ShareCard({ block }: { block: ChatBlock }) {
  const navigate = useNavigate();
  const [copied, setCopied] = useState('');
  const urls = (block.urls ?? {}) as Record<string, string>;
  const shareUrl = String(block.share_url ?? urls.html ?? '');
  const copy = async (label: string, text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(label); setTimeout(() => setCopied(''), 1500); }
    catch { /* clipboard unavailable */ }
  };
  const copyForAi = async () => {
    if (!urls.md) return;
    try { const r = await fetch(urls.md); await navigator.clipboard.writeText(await r.text()); setCopied('ai'); setTimeout(() => setCopied(''), 1500); }
    catch { copy('ai', urls.md); }
  };
  const FMT: Record<string, string> = { md: 'Markdown', html: 'HTML', json: 'JSON', junit: 'JUnit' };
  const integrations = ['Slack', 'Jira', 'Confluence', 'Xray'];
  return (
    <Card tone="brand">
      <div className="flex items-center gap-2">
        <Share2 size={13} className="text-accent" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">Report shared · {block.target}</span>
        {block.public ? <Chip tone="good">public</Chip> : <Chip tone="neutral">private</Chip>}
        {block.stakeholder ? <Chip tone="warn">stakeholder</Chip> : null}
      </div>
      {shareUrl && (
        <div className="mt-2 flex items-center gap-1.5">
          <button onClick={() => window.open(shareUrl, '_blank', 'noopener')}
            className="min-w-0 flex-1 truncate rounded border border-line bg-surface-2 px-2 py-1 text-left text-[11px] text-accent hover:border-accent">
            {shareUrl}
          </button>
          <Button size="sm" variant="subtle" onClick={() => copy('link', shareUrl)}>{copied === 'link' ? 'Copied' : 'Copy link'}</Button>
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {(block.formats as string[] ?? []).map((f) => (
          <button key={f} onClick={() => window.open(urls[f], '_blank', 'noopener')}
            className="rounded border border-line px-2 py-0.5 text-[11px] text-ink-2 hover:border-accent hover:text-ink">
            {FMT[f] ?? f}
          </button>
        ))}
        {/* Never offer a PDF that isn't there; say why instead. */}
        {typeof block.pdf_status === 'string' && block.pdf_status.startsWith('failed') && (
          <span title={block.pdf_status as string} className="rounded border border-fail/40 px-2 py-0.5 text-[10.5px] text-fail">PDF unavailable</span>
        )}
        <Button size="sm" variant="primary" onClick={copyForAi}>{copied === 'ai' ? 'Copied' : 'Copy for AI'}</Button>
      </div>
      <div className="mt-2.5 border-t border-line/60 pt-2">
        <p className="mb-1 text-[10.5px] text-ink-3">Route to a team tool (connect + approval required):</p>
        <div className="flex flex-wrap gap-1.5">
          {integrations.map((i) => (
            <Button key={i} size="sm" variant="ghost" onClick={() => navigate('/settings')}>{i}</Button>
          ))}
        </div>
      </div>
    </Card>
  );
}

/** The Readiness Go/No-Go gate: each criterion evaluated with its actual value,
 *  threshold and evidence, and a human sign-off (the agent can never sign). */
function ReadinessCard({ block }: { block: ChatBlock }) {
  const { project } = useApp();
  const navigate = useNavigate();
  const [signoff, setSignoff] = useState((block.signoff ?? null) as null | { verdict: string; signer_name: string; at: string; override: boolean; override_note?: string | null });
  const [note, setNote] = useState('');
  const [showNote, setShowNote] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const criteria = (block.criteria ?? []) as { key: string; label: string; pass: boolean; actual: string; threshold: string; evidence?: string }[];
  const verdict = String(block.verdict ?? 'no_go');
  const isGo = verdict === 'go';

  const sign = async () => {
    if (!project || !block.run_id) return;
    if (!isGo && !note.trim()) { setShowNote(true); return; }
    setBusy(true); setErr('');
    try {
      const r = await api.post<{ ok: boolean; signoff: typeof signoff }>(
        `/api/projects/${project.id}/runs/${block.run_id}/readiness/sign-off`,
        { override_note: note.trim() || undefined });
      setSignoff(r.signoff);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'sign-off failed');
    } finally { setBusy(false); }
  };

  return (
    <Card tone={isGo ? 'brand' : 'danger'}>
      <div className="flex items-center gap-2">
        {isGo ? <ShieldCheck size={13} className="text-accent" /> : <ShieldAlert size={13} className="text-fail" />}
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">Readiness · {block.target}</span>
        <Chip tone={isGo ? 'good' : 'danger'}>{isGo ? 'GO' : 'NO-GO'}</Chip>
      </div>
      <ul className="mt-2 space-y-1">
        {criteria.map((c) => (
          <li key={c.key} className="flex items-baseline gap-2 text-[11px]">
            {c.pass ? <Check size={11} className="shrink-0 text-pass" /> : <X size={11} className="shrink-0 text-fail" />}
            <span className="min-w-0 flex-1 truncate text-ink-2">{c.label}</span>
            <span className={`mono shrink-0 tabular-nums ${c.pass ? 'text-ink-3' : 'text-fail'}`}>{c.actual}</span>
            {c.evidence && <button onClick={() => navigate(c.evidence!)} className="shrink-0 text-ink-3 underline decoration-dotted hover:text-ink-2">evidence</button>}
          </li>
        ))}
      </ul>
      {block.auth_hint ? (
        <p className="mt-2 flex items-center gap-1.5 text-[11px] text-warn"><Lock size={11} /> {block.auth_hint as string}</p>
      ) : null}
      {signoff ? (
        <p className="mt-2.5 text-[11px] text-ink-2">
          Signed off <span className="font-medium">{signoff.verdict.toUpperCase()}</span> by {signoff.signer_name}
          {signoff.override && ' (override)'} · {new Date(signoff.at).toLocaleString()}
          {signoff.override_note && <span className="block text-ink-3">“{signoff.override_note}”</span>}
        </p>
      ) : (
        <div className="mt-2.5">
          {showNote && (
            <input value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="Override note (required to ship a No-Go)"
              className="mb-1.5 w-full rounded border border-line bg-surface-2 px-2 py-1 text-[11px] text-ink outline-none focus:border-accent" />
          )}
          <div className="flex flex-wrap items-center gap-1.5">
            <Button size="sm" variant={isGo ? 'primary' : 'subtle'} onClick={sign} disabled={busy}>
              {busy ? 'Signing…' : isGo ? 'Sign off' : 'Sign off (override)'}
            </Button>
            <span className="text-[10.5px] text-ink-3">a human decision (the agent can't sign)</span>
          </div>
          {err && <p className="mt-1 text-[10.5px] text-fail">{err}</p>}
        </div>
      )}
    </Card>
  );
}

/** The Triage board: failures grouped by root-cause signature, each with a
 *  verdict, an RCA class, and a disposition. Done when none is left open. */
const DISPOSITION_ORDER = ['heal', 'rerun', 'mark_expected', 'file_bug', 'quarantine'] as const;
const DISPOSITION_LABEL: Record<string, string> = {
  heal: 'Heal', rerun: 'Rerun ×3', mark_expected: 'Mark expected', file_bug: 'File bug', quarantine: 'Quarantine',
};

function TriageBoard({ block }: { block: ChatBlock }) {
  const groups = (block.groups ?? []) as {
    signature: string; label?: string; rca: string; verdict: string; count: number;
    confidence?: string; suggested: string; units?: string[]; keys?: string[];
    disposition: { disposition: string } | null;
    disposition_reasons?: Record<string, { enabled: boolean; reason?: string }>;
  }[];
  const resolved = !!block.resolved;
  const rcaTone = (rca: string) => (rca === 'app-bug' ? 'danger' : rca === 'flaky' ? 'warn' : rca === 'test-bug' ? 'brand' : 'neutral');
  return (
    <Card tone={resolved ? 'brand' : 'danger'}>
      <div className="flex items-center gap-2">
        <ClipboardList size={13} className={resolved ? 'text-accent' : 'text-fail'} />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">
          Triage · {block.group_count as number} group(s)
        </span>
        <span className="shrink-0 text-[11px] text-ink-3">
          {resolved ? 'all dispositioned' : `${block.open_count as number} open`}
        </span>
      </div>
      <ul className="mt-2 space-y-2">
        {groups.map((g) => {
          const reasons = g.disposition_reasons ?? {};
          return (
            <li key={g.signature} className="rounded-md border border-line/60 p-2" data-signature={g.signature}>
              <div className="flex items-baseline gap-1.5">
                <Chip tone={g.verdict === 'failed' ? 'danger' : g.verdict === 'partial' ? 'warn' : 'neutral'}>{g.verdict}</Chip>
                <Chip tone={rcaTone(g.rca)}>{g.rca}</Chip>
                {/* Human root cause; the signature hash rides along only as a tooltip. */}
                <span className="min-w-0 flex-1 truncate text-[11px] text-ink-2" title={g.signature}>
                  {g.label || g.signature}
                </span>
                {g.confidence && <span className="shrink-0 text-[10px] text-ink-3">{g.confidence} conf.</span>}
                <span className="mono shrink-0 text-[10.5px] text-ink-3">{g.count}×</span>
              </div>
              {g.keys && g.keys.length > 0 && (
                <p className="mono mt-0.5 truncate text-[10px] text-ink-3" title={g.keys.join(', ')}>
                  {g.keys.slice(0, 3).join(', ')}{g.keys.length > 3 ? ` +${g.keys.length - 3}` : ''}
                </p>
              )}
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                {g.disposition ? (
                  <Chip tone="good">→ {DISPOSITION_LABEL[g.disposition.disposition] ?? g.disposition.disposition}</Chip>
                ) : (
                  // Every disposition is shown; unavailable ones are greyed with a reason,
                  // never hidden; confirming a flake or filing a bug stays the tester's call.
                  DISPOSITION_ORDER.map((d) => {
                    const r = reasons[d] ?? { enabled: true };
                    const isSuggested = d === g.suggested;
                    return r.enabled ? (
                      <Button key={d} size="sm" variant={isSuggested ? 'primary' : 'ghost'}
                        onClick={() => runCommand(`${d.replace('_', ' ')} for ${g.signature}`)}>
                        {DISPOSITION_LABEL[d]}
                      </Button>
                    ) : (
                      <span key={d} title={r.reason} className="cursor-not-allowed rounded px-1.5 py-0.5 text-[11px] text-ink-3/50 line-through">
                        {DISPOSITION_LABEL[d]}
                      </span>
                    );
                  })
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {resolved && groups.length > 0 && (
        <p className="mt-2 text-[11px] text-ink-3">Every failure has a disposition. Ready for readiness.</p>
      )}
    </Card>
  );
}

function CtaBlock({ block }: { block: ChatBlock }) {
  const navigate = useNavigate();
  const routes: Record<string, string> = {
    open_requirements: '/requirements', open_runs: '/runs',
    open_tests: '/tests', open_approvals: '/approvals',
  };
  return (
    <Button size="sm" variant="ghost" onClick={() => navigate(routes[block.action] ?? '/')}>
      {block.label}
    </Button>
  );
}

const EXPORT_LABEL: Record<string, string> = {
  test_plan: 'Test Plan', test_cases: 'Test Case register',
  rtm: 'Requirements Traceability Matrix', tcr: 'Test Completion Report',
};

/** A ready download link ("export the test plan", "export test cases as excel")
 *  as a real card - the reply text stays a short sentence, this carries the link. */
function ExportBlock({ block }: { block: ChatBlock }) {
  const label = EXPORT_LABEL[String(block.artifact ?? '')] ?? 'Document';
  const format = String(block.format ?? '').toUpperCase();
  return (
    <Card tone="brand">
      <a href={String(block.url ?? '')} download className="flex items-center gap-2.5">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-accent/15 text-accent">
          <Download size={14} />
        </span>
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">{label}</span>
        {format && <Chip tone="brand">{format}</Chip>}
      </a>
    </Card>
  );
}

function ErrorBlock({ block }: { block: ChatBlock }) {
  return (
    <Card tone="danger">
      <div className="flex items-center gap-2 text-[12px] text-fail">
        <AlertTriangle size={13} /> That didn't work
      </div>
      {block.detail?.problems && (
        <ul className="mt-1 space-y-0.5">
          {block.detail.problems.map((p: string) => (
            <li key={p} className="text-[11px] text-ink-3">· {p}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// --- WO#5 release-management cards ----------------------------------------- #
const pct = (x: number) => `${Math.round((x ?? 0) * 1000) / 10}%`;

function MilestoneCard({ block }: { block: ChatBlock }) {
  const signed = block.signoff as null | { by: string; decision: string; note?: string };
  const released = block.status === 'released';
  return (
    <Card tone={released ? 'brand' : undefined}>
      <div className="flex items-center gap-2">
        <Rocket size={13} className="text-accent" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">
          Release {block.version}: {block.name}
        </span>
        <Chip tone={released ? 'good' : 'neutral'}>{block.status}</Chip>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-3">
        {block.target_date && <span><Calendar size={11} className="mr-1 inline" />due {String(block.target_date).slice(0, 10)}</span>}
        {(block.exit_criteria ?? []).length > 0 && <span>{block.exit_criteria.length} exit criteria</span>}
      </div>
      {(block.exit_criteria ?? []).length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {block.exit_criteria.map((c: any, i: number) => (
            <li key={i} className="mono text-[10.5px] text-ink-2">{c.metric} {c.op} {c.value}</li>
          ))}
        </ul>
      )}
      {signed && (
        <div className="mt-2 rounded-md border border-line bg-surface-3 px-2 py-1 text-[11px] text-ink-2">
          Signed off <b className="text-ink">{signed.decision.toUpperCase()}</b> by {signed.by}
          {signed.note ? `: “${signed.note}”` : ''} · immutable
        </div>
      )}
    </Card>
  );
}

function EnvironmentCard({ block }: { block: ChatBlock }) {
  return (
    <Card>
      <div className="flex items-center gap-2">
        <ServerIcon size={13} className="text-ink-3" />
        <span className="text-[12px] font-medium text-ink">{block.name}</span>
        <a href={block.base_url} target="_blank" rel="noreferrer" className="mono truncate text-[11px] text-accent hover:underline">{block.base_url}</a>
      </div>
      {(block.tags ?? []).length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">{block.tags.map((t: string) => <Chip key={t}>{t}</Chip>)}</div>
      )}
    </Card>
  );
}

function PlanCard({ block }: { block: ChatBlock }) {
  const configs = (block.configurations ?? []) as { browser: string; viewport: string }[];
  return (
    <Card>
      <div className="flex items-center gap-2">
        <ClipboardList size={13} className="text-ink-3" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">{block.name}</span>
        <Chip tone="brand">{block.case_count} case(s)</Chip>
      </div>
      <div className="mt-1.5 text-[11px] text-ink-3">Configuration matrix: {block.matrix_size} runs</div>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {configs.map((c, i) => (
          <span key={i} className="mono rounded-md border border-line bg-surface-2 px-1.5 py-0.5 text-[10.5px] text-ink-2">
            {c.browser}/{c.viewport}
          </span>
        ))}
      </div>
    </Card>
  );
}

function CycleCard({ block }: { block: ChatBlock }) {
  const cfg = block.configuration ?? {};
  const cnt = block.counters ?? {};
  return (
    <Card>
      <div className="flex items-center gap-2">
        <RotateCw size={13} className="text-ink-3" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">{cfg.browser}/{cfg.viewport}</span>
        <Chip tone={block.status === 'complete' ? 'good' : 'neutral'}>{block.status}</Chip>
      </div>
      <div className="mt-1.5 flex items-center gap-3 text-[11px] text-ink-3">
        <span>{block.case_count} case(s)</span>
        <span>{cnt.executed ?? 0}/{cnt.planned ?? 0} executed</span>
      </div>
    </Card>
  );
}

function ReleaseReadinessCard({ block }: { block: ChatBlock }) {
  const isGo = block.verdict === 'go';
  const criteria = (block.criteria ?? []) as { metric: string; op: string; target: any; actual: any; met: boolean }[];
  return (
    <Card tone={isGo ? 'brand' : 'danger'}>
      <div className="flex items-center gap-2">
        {isGo ? <ShieldCheck size={13} className="text-accent" /> : <ShieldAlert size={13} className="text-fail" />}
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">Release {block.version} readiness</span>
        <Chip tone={isGo ? 'good' : 'danger'}>{isGo ? 'GO' : 'NO-GO'}</Chip>
      </div>
      <ul className="mt-2 space-y-1">
        {criteria.map((c, i) => (
          <li key={i} className="flex items-baseline gap-2 text-[11px]">
            {c.met ? <Check size={11} className="shrink-0 text-pass" /> : <X size={11} className="shrink-0 text-fail" />}
            <span className="min-w-0 flex-1 truncate text-ink-2">{c.metric} {c.op} {c.target}</span>
            <span className={clsx('mono shrink-0 tabular-nums', c.met ? 'text-ink-3' : 'text-fail')}>{c.actual}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ReleaseReportCard({ block }: { block: ChatBlock }) {
  const m = block.metrics ?? {};
  const ms = block.milestone ?? {};
  const isGo = block.readiness?.verdict === 'go';
  const row = (label: string, value: string) => (
    <div className="flex items-baseline justify-between gap-2"><span className="text-ink-3">{label}</span><span className="mono tabular-nums text-ink-2">{value}</span></div>
  );
  return (
    <Card tone={isGo ? 'brand' : undefined}>
      <div className="flex items-center gap-2">
        <FileText size={13} className="text-ink-3" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">Release report: {ms.version}</span>
        <Chip tone={isGo ? 'good' : 'danger'}>{isGo ? 'GO' : 'NO-GO'}</Chip>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
        {row('Execution', pct(m.execution_progress))}
        {row('Pass rate', pct(m.pass_rate))}
        {row('Req coverage', pct(m.requirement_coverage))}
        {row('P1 coverage', pct(m.p1_requirement_coverage))}
        {row('Automation', pct(m.automation_ratio))}
        {row('Flaky', pct(m.flaky_rate))}
        {row('Open blockers', String(m.open_blockers ?? 0))}
        {row('Defect density', String(m.defect_density ?? 0))}
      </div>
    </Card>
  );
}
