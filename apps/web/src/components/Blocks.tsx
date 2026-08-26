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
  AlertTriangle, Ban, Calendar, Check, ChevronDown, FlaskConical, Info,
  RotateCw, ShieldAlert, Sparkles, Target, X,
} from 'lucide-react';
import { api, type ChatBlock } from '../lib/api';
import { RISK_COLOR, duration, statusMeta } from '../lib/format';
import { useApp } from '../state';
import { Button, Chip, Meter, StatusPill } from './primitives';

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
    case 'rca': return <RcaBlock block={block} />;
    case 'mode_notice': return <ModeNotice block={block} />;
    case 'smoke_result': return <SmokeResult block={block} />;
    case 'cta': return <CtaBlock block={block} />;
    case 'error': return <ErrorBlock block={block} />;
    default: return null;
  }
}

function Card({ children, tone }: { children: React.ReactNode; tone?: 'brand' | 'warn' | 'danger' }) {
  const tones = {
    brand: 'border-accent/25 bg-accent/[0.06]',
    warn: 'border-flaky/25 bg-flaky/[0.06]',
    danger: 'border-fail/25 bg-fail/[0.06]',
  };
  return (
    <div className={clsx('rounded-xl border p-2.5', tone ? tones[tone] : 'border-line bg-surface-2')}>
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
          {omitted.length} omitted — shown in full, never silently dropped
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
          {state === 'approved' ? 'Approved and applied.' : 'Rejected — nothing changed.'}
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
        The assistant proposed <code className="mono text-ink-2">{block.tool}</code>. It has not run —
        nothing changes until you approve it.
      </p>
      {block.arguments && (
        <pre className="rounded-md mono mt-2 max-h-28 overflow-auto bg-canvas/60 p-2 text-[10.5px] text-ink-3">
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
        <span className="text-[12px] font-medium text-ink-2">Available without a model</span>
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

function SmokeResult({ block }: { block: ChatBlock }) {
  const navigate = useNavigate();
  const ok = !!block.ok;
  const consoleErrors = (block.console_errors ?? []).length;
  const networkFailures = (block.network_failures ?? []).length;
  return (
    <Card tone={ok ? undefined : 'danger'}>
      <div className="flex items-center gap-2">
        {ok
          ? <Check size={13} className="text-pass" />
          : <X size={13} className="text-fail" />}
        <span className="text-[12px] font-medium text-ink-2">
          {ok ? 'Smoke check passed' : 'Smoke check failed'}
        </span>
        {block.run_number ? (
          <span className="ml-auto text-[11px] text-ink-3">run #{block.run_number}</span>
        ) : null}
      </div>
      <div className="mt-1 flex items-baseline gap-1.5 text-[11.5px] text-ink-3">
        <Target size={10} className="shrink-0" /> {block.target}
      </div>
      {(consoleErrors > 0 || networkFailures > 0) && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {consoleErrors > 0 && (
            <Chip tone="warn">{consoleErrors} console error{consoleErrors > 1 ? 's' : ''}</Chip>
          )}
          {networkFailures > 0 && (
            <Chip tone="warn">{networkFailures} server (5xx)</Chip>
          )}
        </div>
      )}
      {block.run_id && (
        <div className="mt-2">
          <Button size="sm" variant="ghost" onClick={() => navigate('/runs')}>View run</Button>
        </div>
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
