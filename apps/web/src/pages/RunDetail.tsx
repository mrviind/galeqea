import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import clsx from 'clsx';
import {
  ArrowLeft, Ban, ChevronRight, Download, RotateCw, Sparkles, Target, Wrench,
} from 'lucide-react';
import { api } from '../lib/api';
import type { RunDetail as RunDetailType, RunResult } from '../lib/api';
import { duration } from '../lib/format';
import { useApp, useEvents } from '../state';
import { LiveLog } from '../components/LiveLog';
import { Button, Chip, Empty, Panel, SectionTitle, Spinner, StatusPill } from '../components/primitives';

export default function RunDetail() {
  const { runId } = useParams();
  const { project } = useApp();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<RunDetailType | null>(null);
  const [selected, setSelected] = useState<RunResult | null>(null);
  const [steps, setSteps] = useState<any[]>([]);
  const [rca, setRca] = useState<any | null>(null);
  const [rcaBusy, setRcaBusy] = useState(false);

  const load = useCallback(() => {
    if (!project || !runId) return;
    api.get<RunDetailType>(`/api/projects/${project.id}/runs/${runId}`).then(setDetail).catch(() => {});
  }, [project, runId]);

  useEffect(load, [load]);
  useEvents(['run.test.finished', 'run.finished', 'run.step'], (e) => {
    if (e.run_id === runId) load();
  });

  useEffect(() => {
    if (!project || !runId || !selected) { setSteps([]); return; }
    api.get<any[]>(`/api/projects/${project.id}/runs/${runId}/results/${selected.id}/steps`)
      .then(setSteps).catch(() => setSteps([]));
    setRca(null);
  }, [project, runId, selected]);

  const analyze = async () => {
    if (!project || !selected) return;
    setRcaBusy(true);
    try {
      setRca(await api.post<any>(`/api/projects/${project.id}/rca`, { run_test_id: selected.id }));
    } finally { setRcaBusy(false); }
  };

  const rerun = async (failedOnly: boolean) => {
    if (!project || !runId) return;
    const res = await api.post<any>(`/api/projects/${project.id}/runs/${runId}/rerun`, { failed_only: failedOnly });
    navigate(`/runs/${res.id}`);
  };

  if (!detail) {
    return <div className="flex h-40 items-center justify-center text-ink-3"><Spinner /></div>;
  }

  const { run, results, artifacts } = detail;
  const triage = run.triage ?? {};
  const live = run.status === 'running' || run.status === 'queued';

  return (
    <div className="space-y-3 p-3">
      {/* --- header ------------------------------------------------------ */}
      <Panel glow={live} className="p-3">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/runs')} className="rounded-lg p-1 text-ink-3 transition hover:bg-surface-2 hover:text-ink">
            <ArrowLeft size={15} />
          </button>
          <StatusPill status={run.status} live={live} />
          <div className="min-w-0">
            <h1 className="truncate text-[14px] font-semibold">Run #{run.number} · {run.title}</h1>
            <p className="truncate text-[11px] text-ink-3">
              {run.trigger} · {run.environment} · {run.base_url} · {(run.browsers ?? []).join(', ')}
              {run.command && <> · “{run.command}”</>}
            </p>
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-1.5">
            <span className="mono mr-1 text-[11px] text-ink-3">{duration(run.duration_ms)}</span>
            <Button size="sm" onClick={() => rerun(false)}><RotateCw size={11} /> Run again</Button>
            <Button size="sm" onClick={() => rerun(true)} disabled={!results.some((r) => ['failed', 'error'].includes(r.status))}>
              <Target size={11} /> Only failed
            </Button>
            {live && (
              <Button size="sm" variant="danger" onClick={() => api.post(`/api/projects/${project!.id}/runs/${run.id}/cancel`)}>
                <Ban size={11} /> Cancel
              </Button>
            )}
          </div>
        </div>

        {run.error && (
          <p className="rounded-lg mt-2 border border-fail/25 bg-fail/[0.07] px-3 py-2 text-[12px] leading-relaxed text-fail">
            {run.error}
          </p>
        )}

        {triage.headline && (
          <p className="mt-2 text-[12.5px] text-ink-2">{triage.headline}</p>
        )}

        {/* Triage counters make "is anything actually new?" a one-glance question. */}
        <div className="mt-2 flex flex-wrap gap-1.5">
          {[
            ['new', 'danger'], ['known', 'neutral'], ['flaky', 'warn'],
            ['environment', 'neutral'], ['test_defect', 'warn'], ['needs_review', 'neutral'],
          ].map(([key, tone]) => {
            const list = triage[key as string];
            if (!Array.isArray(list) || list.length === 0) return null;
            return <Chip key={key} tone={tone as any}>{list.length} {String(key).replace('_', ' ')}</Chip>;
          })}
        </div>
      </Panel>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        {/* --- results ---------------------------------------------------- */}
        <Panel className="overflow-hidden">
          <SectionTitle hint={`${results.length} result(s)`}>Results</SectionTitle>
          <div className="max-h-[520px] overflow-y-auto border-t border-line">
            {results.length === 0 && <Empty title="No results yet" body="They stream in as each test finishes." />}
            {results.map((result) => (
              <button
                key={result.id}
                onClick={() => setSelected(result)}
                className={clsx(
                  'flex w-full items-center gap-2 border-b border-line/60 px-3 py-2 text-left transition last:border-0',
                  selected?.id === result.id ? 'bg-surface-2' : 'hover:bg-surface-2/60',
                )}
              >
                <StatusPill status={result.status} />
                <span className="mono w-24 shrink-0 truncate text-ink-3">{result.key}</span>
                <span className="min-w-0 flex-1 truncate text-[12px] text-ink-2">{result.title}</span>
                {result.healed && <Wrench size={11} className="shrink-0 text-flaky" />}
                {result.classification && (
                  <Chip tone={result.classification === 'new' ? 'danger' : 'neutral'}>{result.classification}</Chip>
                )}
                <span className="mono w-12 shrink-0 text-right text-[10.5px] text-ink-3">{duration(result.duration_ms)}</span>
                <ChevronRight size={12} className="shrink-0 text-ink-3" />
              </button>
            ))}
          </div>
        </Panel>

        {/* --- detail / live log ------------------------------------------ */}
        {selected ? (
          <Panel className="flex max-h-[560px] flex-col overflow-hidden">
            <SectionTitle
              hint={selected.key}
              action={
                <Button size="sm" variant="ghost" onClick={analyze} disabled={rcaBusy}>
                  {rcaBusy ? <Spinner /> : <Sparkles size={11} />} Explain this failure
                </Button>
              }
            >
              {selected.title}
            </SectionTitle>

            <div className="min-h-0 flex-1 overflow-y-auto border-t border-line px-3 py-2">
              {selected.error_message && (
                <pre className="rounded-md mono mb-3 whitespace-pre-wrap border border-fail/25 bg-fail/[0.06] p-2.5 text-[11px] leading-relaxed text-fail">
                  {selected.error_message}
                </pre>
              )}

              {rca && <RcaPanel rca={rca} />}

              <ol className="space-y-px">
                {steps.map((step) => (
                  <li key={step.index} className="rounded-md flex items-baseline gap-2 px-1 py-1 hover:bg-surface-2">
                    <span className="mono w-5 shrink-0 text-right text-[10px] text-ink-3">{step.index}</span>
                    <StatusPill status={step.status} className="shrink-0" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12px] text-ink-2">{step.intent || step.action}</span>
                      {step.resolved_locator && (
                        <span className="mono block truncate text-[10px] text-ink-3">{step.resolved_locator}</span>
                      )}
                      {step.heal_applied && (
                        <span className="mono mt-0.5 block truncate text-[10px] text-flaky">
                          ⟲ healed via {step.heal_applied.strategy}: {step.heal_applied.from} → {step.heal_applied.to}
                        </span>
                      )}
                      {step.error_message && (
                        <span className="block text-[10.5px] leading-relaxed text-fail">{step.error_message}</span>
                      )}
                    </span>
                    <span className="mono w-11 shrink-0 text-right text-[10px] text-ink-3">{step.duration_ms}ms</span>
                  </li>
                ))}
              </ol>

              {(selected.console_errors?.length > 0 || selected.network_failures?.length > 0) && (
                <div className="mt-3 space-y-2 border-t border-line pt-2">
                  {selected.console_errors?.length > 0 && (
                    <Diagnostics title="Console errors" items={selected.console_errors.map((c: any) => c.text)} />
                  )}
                  {selected.network_failures?.length > 0 && (
                    <Diagnostics
                      title="Failed requests"
                      items={selected.network_failures.map((n: any) => `${n.status ?? n.failure} ${n.url}`)}
                    />
                  )}
                </div>
              )}

              <ArtifactList
                artifacts={artifacts.filter((a) => a.run_test_id === selected.id)}
                projectId={project!.id}
                runId={run.id}
              />
            </div>
          </Panel>
        ) : (
          <Panel className="flex max-h-[560px] flex-col overflow-hidden">
            <LiveLog runId={run.id} className="flex-1" />
          </Panel>
        )}
      </div>
    </div>
  );
}

function RcaPanel({ rca }: { rca: any }) {
  return (
    <div className="rounded-lg mb-3 border border-accent/25 bg-accent/[0.06] p-2.5">
      <div className="flex items-center gap-2">
        <Sparkles size={13} className="text-accent" />
        <span className="text-[12px] font-medium">Root cause · {rca.category}</span>
        <span className="mono ml-auto text-[10.5px] text-ink-3">
          {Math.round(rca.confidence * 100)}% · {rca.generated_by}
        </span>
      </div>
      <p className="mt-1.5 text-[12px] leading-relaxed text-ink-2">{rca.summary}</p>
      {rca.suggested_fix && <p className="mt-1.5 text-[11.5px] text-accent">→ {rca.suggested_fix}</p>}
      <div className="mt-2 space-y-1 border-t border-line/60 pt-2">
        {(rca.evidence ?? []).map((e: any) => (
          <div key={e.id} className="flex items-baseline gap-2 text-[11px]">
            <span className="mono shrink-0 text-accent">{e.id}</span>
            <span className="text-ink-3">{e.summary}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Diagnostics({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <p className="mb-1 text-[11px] font-medium text-ink-3">{title}</p>
      <ul className="space-y-0.5">
        {items.slice(0, 6).map((item, i) => (
          <li key={i} className="mono truncate text-[10.5px] text-flaky">{item}</li>
        ))}
      </ul>
    </div>
  );
}

function ArtifactList({ artifacts, projectId, runId }: { artifacts: any[]; projectId: string; runId: string }) {
  if (artifacts.length === 0) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-1.5 border-t border-line pt-2">
      {artifacts.map((a) => (
        <a
          key={a.id}
          href={`/api/projects/${projectId}/runs/${runId}/artifacts/${a.id}`}
          className="rounded-lg flex items-center gap-1.5 border border-line bg-surface-2 px-2 py-1 text-[11px] text-ink-2 transition hover:border-accent/30 hover:text-ink"
        >
          <Download size={10} /> {a.kind}{a.label ? ` · ${a.label}` : ''}
        </a>
      ))}
    </div>
  );
}
