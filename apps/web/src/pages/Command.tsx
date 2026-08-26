import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import {
  Activity, AlertTriangle, CheckCircle2, ClipboardCheck, HandHelping,
  ShieldCheck, TrendingUp, Wrench,
} from 'lucide-react';
import { api } from '../lib/api';
import { duration, pct, relative } from '../lib/format';
import { useApp, useEvents } from '../state';
import { LiveLog } from '../components/LiveLog';
import { Button, Empty, Meter, Panel, SectionTitle, StatusPill } from '../components/primitives';

export default function Command() {
  const { overview, project } = useApp();
  const navigate = useNavigate();
  const [handoff, setHandoff] = useState<any | null>(null);
  const [liveRun, setLiveRun] = useState<string | null>(null);

  useEvents(['run.started', 'run.finished', 'run.handoff'], (event) => {
    if (event.type === 'run.handoff') setHandoff(event.payload);
    if (event.type === 'run.started') { setLiveRun(event.run_id ?? null); setHandoff(null); }
    if (event.type === 'run.finished') setLiveRun(null);
  });

  const recent = overview?.runs.recent ?? [];
  const passRate = overview?.runs.pass_rate ?? 0;

  const trend = useMemo(
    () => recent.slice(0, 14).reverse().map((r) => {
      const total = r.totals?.total || 1;
      return { id: r.id, value: (r.totals?.passed ?? 0) / total, status: r.status, number: r.number };
    }),
    [recent],
  );

  return (
    <div className="flex min-h-full flex-col gap-3 p-3">
      {handoff && <HandoffBanner handoff={handoff} onResolved={() => setHandoff(null)} />}

      {/* --- metric strip ------------------------------------------------ */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric
          icon={<TrendingUp size={14} />}
          label="Pass rate"
          value={pct(passRate)}
          hint={`${recent.length} recent run(s)`}
          meter={passRate * 100}
          tone={passRate > 0.9 ? 'pass' : passRate > 0.7 ? 'flaky' : 'fail'}
        />
        <Metric
          icon={<ClipboardCheck size={14} />}
          label="Requirement coverage"
          value={`${overview?.coverage.coverage_pct ?? 0}%`}
          hint={`${overview?.coverage.uncovered.length ?? 0} gap(s)`}
          meter={overview?.coverage.coverage_pct ?? 0}
          tone={(overview?.coverage.coverage_pct ?? 0) > 80 ? 'pass' : 'flaky'}
          onClick={() => navigate('/requirements')}
        />
        <Metric
          icon={<ShieldCheck size={14} />}
          label="Awaiting your approval"
          value={String(overview?.approvals_pending ?? 0)}
          hint={overview?.approvals_pending ? 'nothing changes until you decide' : 'queue is clear'}
          tone={overview?.approvals_pending ? 'flaky' : 'pass'}
          onClick={() => navigate('/approvals')}
        />
        <Metric
          icon={<Activity size={14} />}
          label="Tests"
          value={String(overview?.tests.total ?? 0)}
          hint={`${overview?.tests.awaiting_review ?? 0} proposed · ${overview?.tests.by_category?.automated ?? 0} automated`}
          onClick={() => navigate('/tests')}
        />
      </div>

      <div className="grid min-h-0 flex-1 gap-3 xl:grid-cols-[1.15fr_1fr]">
        {/* --- live theatre --------------------------------------------- */}
        <Panel glow={Boolean(liveRun)} className="flex min-h-[380px] flex-col overflow-hidden">
          <LiveLog runId={undefined} className="flex-1" />
        </Panel>

        <div className="flex min-h-0 flex-col gap-3">
          {/* --- run history ------------------------------------------- */}
          <Panel className="overflow-hidden">
            <SectionTitle
              hint={`${recent.length} most recent`}
              action={<Button size="sm" variant="subtle" onClick={() => navigate('/runs')}>All runs</Button>}
            >
              Runs
            </SectionTitle>
            {trend.length > 0 && (
              <div className="flex h-9 items-end justify-start gap-1 px-4 pb-2">
                {trend.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => navigate(`/runs/${t.id}`)}
                    title={`Run #${t.number} — ${pct(t.value)} passed`}
                    className="group relative w-full max-w-[26px] flex-1 transition-all hover:opacity-100"
                    style={{ height: `${Math.max(12, t.value * 100)}%` }}
                  >
                    <span className={clsx(
                      'block h-full w-full opacity-70 transition group-hover:opacity-100',
                      t.status === 'passed' ? 'bg-pass'
                        : t.status === 'flaky' ? 'bg-flaky'
                        : t.status === 'failed' || t.status === 'error' ? 'bg-fail'
                        : 'bg-ink-3',
                    )} />
                  </button>
                ))}
              </div>
            )}
            <div className="max-h-[210px] overflow-y-auto border-t border-line">
              {recent.length === 0 && (
                <Empty
                  title="No runs yet"
                  body='Type "run the smoke tests" in the assistant, or approve a test and run it from the Tests page.'
                />
              )}
              {recent.slice(0, 8).map((run) => (
                <button
                  key={run.id}
                  onClick={() => navigate(`/runs/${run.id}`)}
                  className="flex w-full items-center gap-2.5 border-b border-line/60 px-4 py-2 text-left transition last:border-0 hover:bg-surface-2"
                >
                  <StatusPill status={run.status} live={run.status === 'running'} />
                  <span className="mono shrink-0 text-ink-3">#{run.number}</span>
                  <span className="min-w-0 flex-1 truncate text-[12px] text-ink-2">
                    {run.headline || run.title}
                  </span>
                  <span className="shrink-0 text-[10.5px] text-ink-3">{duration(run.duration_ms)}</span>
                  <span className="w-14 shrink-0 text-right text-[10.5px] text-ink-3">{relative(run.created_at)}</span>
                </button>
              ))}
            </div>
          </Panel>

          {/* --- attention list ---------------------------------------- */}
          <Panel className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <SectionTitle hint="worst first">Needs attention</SectionTitle>
            <div className="min-h-0 flex-1 space-y-px overflow-y-auto pb-2">
              {(overview?.coverage.uncovered ?? [])
                .filter((g: any) => ['critical', 'high'].includes(g.risk))
                .slice(0, 3)
                .map((gap: any) => (
                  <Attention
                    key={gap.ref}
                    icon={<AlertTriangle size={12} className="text-flaky" />}
                    label={`${gap.ref} has no approved test`}
                    detail={gap.title}
                    onClick={() => navigate('/requirements')}
                  />
                ))}
              {(overview?.flaky ?? []).slice(0, 3).map((f) => (
                <Attention
                  key={f.key}
                  icon={<Wrench size={12} className="text-flaky" />}
                  label={`${f.key} is unstable (${Math.round(f.score * 100)}%)`}
                  detail={f.title}
                  onClick={() => navigate('/intelligence')}
                />
              ))}
              {(overview?.approvals_pending ?? 0) > 0 && (
                <Attention
                  icon={<ShieldCheck size={12} className="text-accent" />}
                  label={`${overview?.approvals_pending} change(s) waiting on you`}
                  detail="Nothing has been applied."
                  onClick={() => navigate('/approvals')}
                />
              )}
              {!overview?.coverage.uncovered.length && !overview?.flaky.length && !overview?.approvals_pending && (
                <div className="flex items-center gap-2 px-4 py-3 text-[12px] text-pass">
                  <CheckCircle2 size={13} /> Nothing needs your attention.
                </div>
              )}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function Metric({
  icon, label, value, hint, meter, tone = 'brand', onClick,
}: {
  icon: React.ReactNode; label: string; value: string; hint?: string;
  meter?: number; tone?: string; onClick?: () => void;
}) {
  const Tag = onClick ? 'button' : 'div';
  return (
    <Tag
      onClick={onClick}
      className={clsx(
        'panel p-3 text-left transition',
        onClick && 'hover:border-line-strong hover:bg-surface-2',
      )}
    >
      <div className="flex items-center gap-1.5 text-ink-3">
        {icon}
        <span className="text-[11px]">{label}</span>
      </div>
      <p className="mt-1.5 text-2xl font-semibold tracking-tight tabular-nums">{value}</p>
      <div className="mt-2 h-1.5">
        {meter !== undefined && <Meter value={meter} tone={tone} />}
      </div>
      {hint && <p className="mt-1.5 text-[10.5px] text-ink-3">{hint}</p>}
    </Tag>
  );
}

function Attention({ icon, label, detail, onClick }: {
  icon: React.ReactNode; label: string; detail?: string; onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="rounded-lg flex w-full items-baseline gap-2 px-4 py-1.5 text-left transition hover:bg-surface-2"
    >
      <span className="mt-0.5 shrink-0">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12px] text-ink-2">{label}</span>
        {detail && <span className="block truncate text-[10.5px] text-ink-3">{detail}</span>}
      </span>
    </button>
  );
}

/**
 * Pause-and-attach: the browser is parked mid-run waiting for a person to clear
 * a blocker the automation cannot pass (SSO, MFA, a CAPTCHA, a shadow-DOM
 * widget). Without this, those journeys are simply declared untestable.
 */
function HandoffBanner({ handoff, onResolved }: { handoff: any; onResolved: () => void }) {
  const { project } = useApp();
  const [busy, setBusy] = useState(false);

  const resume = async () => {
    if (!project) return;
    setBusy(true);
    try {
      await api.post(`/api/projects/${project.id}/runs/${handoff.run_id ?? ''}/resume-handoff`, {
        handoff_key: handoff.handoff_key,
      });
      onResolved();
    } finally { setBusy(false); }
  };

  return (
    <Panel glow className="flex items-center gap-3 border-blocked/30 bg-blocked/[0.07] p-3">
      <HandHelping size={18} className="shrink-0 text-blocked" />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium text-ink">The run is paused and waiting for you</p>
        <p className="mt-0.5 text-[11.5px] leading-relaxed text-ink-2">
          {handoff.reason || 'A step needs manual intervention.'}{' '}
          {handoff.instructions}{' '}
          <span className="text-ink-3">The browser is live at {handoff.url}</span>
        </p>
      </div>
      <Button variant="primary" onClick={resume} disabled={busy}>
        {busy ? 'Resuming…' : 'I’ve handled it — resume'}
      </Button>
    </Panel>
  );
}
