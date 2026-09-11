import { useCallback, useEffect, useState } from 'react';
import { Calendar, Check, Rocket, ShieldAlert, ShieldCheck, X } from 'lucide-react';
import {
  releases, type Environment, type Milestone, type ReleaseMetrics, type Readiness,
} from '../lib/api';
import { useApp } from '../state';
import { Button, Chip, Empty, Meter, Panel, SectionTitle, Spinner } from '../components/primitives';

const pct = (x: number) => `${Math.round((x ?? 0) * 1000) / 10}%`;

export default function Releases() {
  const { project } = useApp();
  const [list, setList] = useState<Milestone[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!project) return;
    releases.list(project.id).then((r) => {
      setList(r.milestones);
      setSelected((s) => s ?? r.milestones[0]?.id ?? null);
    }, () => setList([]));
  }, [project]);

  useEffect(() => { load(); }, [load]);

  if (!project) return null;
  const current = list?.find((m) => m.id === selected) ?? null;

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(0,340px)_minmax(0,1fr)] gap-3 p-3">
      {/* --- milestone list --- */}
      <Panel className="flex min-h-0 flex-col overflow-hidden">
        <SectionTitle hint="create one in chat: “create release 1.4”">Releases</SectionTitle>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {list === null && <div className="p-4"><Spinner className="text-ink-3" /></div>}
          {list?.length === 0 && (
            <Empty icon={<Rocket size={16} />} title="No releases yet"
                   body="Say “create release 1.4 due Sept 15” in the chat." />
          )}
          {list?.map((m) => (
            <MilestoneRow key={m.id} m={m} pid={project.id}
                          active={m.id === selected} onClick={() => setSelected(m.id)} />
          ))}
        </div>
      </Panel>

      {/* --- selected milestone detail --- */}
      <div className="min-h-0 overflow-y-auto">
        {current ? <MilestoneDetail key={current.id} m={current} pid={project.id} onChange={load} />
                 : <Panel className="p-6"><Empty title="Select a release" /></Panel>}
      </div>
    </div>
  );
}

function MilestoneRow({ m, pid, active, onClick }: { m: Milestone; pid: string; active: boolean; onClick: () => void }) {
  const [ready, setReady] = useState<string | null>(null);
  const [prog, setProg] = useState<number | null>(null);
  useEffect(() => {
    releases.readiness(pid, m.id).then((r) => setReady(r.verdict), () => {});
    releases.metrics(pid, m.id).then((x) => setProg(x.execution_progress), () => {});
  }, [pid, m.id]);
  return (
    <button onClick={onClick}
            className={`w-full border-b border-line/60 px-3 py-2.5 text-left transition hover:bg-surface-2 ${active ? 'bg-surface-2' : ''}`}>
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink">{m.version} · {m.name}</span>
        {m.signoff ? <Chip tone={m.signoff.decision === 'go' ? 'good' : 'danger'}>signed {m.signoff.decision}</Chip>
          : ready ? <Chip tone={ready === 'go' ? 'good' : 'danger'}>{ready === 'go' ? 'GO' : 'NO-GO'}</Chip>
          : <Chip>{m.status}</Chip>}
      </div>
      <div className="mt-1.5"><Meter value={prog ?? 0} tone={ready === 'go' ? 'good' : 'brand'} /></div>
      <div className="mt-1 flex items-center gap-2 text-[10.5px] text-ink-3">
        {m.target_date && <span><Calendar size={10} className="mr-0.5 inline" />{m.target_date.slice(0, 10)}</span>}
        <span>{prog === null ? '' : `${pct(prog)} executed`}</span>
      </div>
    </button>
  );
}

function MilestoneDetail({ m, pid, onChange }: { m: Milestone; pid: string; onChange: () => void }) {
  const [metrics, setMetrics] = useState<ReleaseMetrics | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [cycles, setCycles] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState('');

  const refresh = useCallback(() => {
    releases.metrics(pid, m.id).then(setMetrics, () => {});
    releases.readiness(pid, m.id).then(setReadiness, () => {});
    releases.cycles(pid, m.id).then((r) => setCycles(r.cycles), () => {});
  }, [pid, m.id]);
  useEffect(() => { refresh(); }, [refresh]);

  const sign = async (decision: 'go' | 'no_go', noteText = '') => {
    setBusy(true); setErr('');
    try { await releases.signoff(pid, m.id, decision, noteText); setNoteOpen(false); setNote(''); onChange(); }
    catch (e: any) { setErr(e?.message || 'sign-off failed'); }
    finally { setBusy(false); }
  };

  const isGo = readiness?.verdict === 'go';
  // A GO while readiness is NO-GO is an override that must carry a note, so open
  // the note dialog instead of signing straight away.
  const clickGo = () => { if (isGo) sign('go'); else setNoteOpen(true); };
  return (
    <div className="space-y-3">
      <Panel className="p-3">
        <div className="flex items-center gap-2">
          <Rocket size={15} className="text-accent" />
          <span className="text-[15px] font-semibold text-ink">Release {m.version}</span>
          <span className="text-[12px] text-ink-3">{m.name}</span>
          <div className="flex-1" />
          {m.signoff ? (
            <Chip tone={m.signoff.decision === 'go' ? 'good' : 'danger'}>
              signed off {m.signoff.decision.toUpperCase()} · {m.signoff.by}
            </Chip>
          ) : (
            <div className="flex items-center gap-2">
              <Button
                variant="primary" disabled={busy} onClick={clickGo}
                title={isGo ? undefined
                  : 'Readiness is NO-GO. A GO sign-off is an override and requires a note'}>
                {isGo ? 'Sign off GO' : 'Override to GO'}
              </Button>
              <Button variant="danger" disabled={busy} onClick={() => sign('no_go')}>NO-GO</Button>
            </div>
          )}
        </div>
        {noteOpen && (
          <div className="mt-2 rounded-md border border-fail/30 bg-fail/5 p-2.5">
            <div className="flex items-center gap-1.5 text-[12px] font-semibold text-fail">
              <ShieldAlert size={13} /> Override: readiness is NO-GO
            </div>
            <p className="mt-1 text-[11.5px] text-ink-3">
              Signing GO against a NO-GO readiness is an override. Record why. This note is
              stored on the immutable sign-off.
            </p>
            <textarea
              value={note} onChange={(e) => setNote(e.target.value)} rows={2} autoFocus
              placeholder="e.g. Hotfix; the two failing checks are known-flaky, tracked in JIRA-1234."
              className="mt-1.5 w-full resize-y rounded border border-line bg-surface-2 px-2 py-1.5 text-[12px] text-ink outline-none focus:border-accent" />
            <div className="mt-1.5 flex items-center justify-end gap-2">
              <Button variant="ghost" disabled={busy} onClick={() => { setNoteOpen(false); setNote(''); }}>
                Cancel
              </Button>
              <Button variant="danger" disabled={busy || note.trim().length === 0}
                onClick={() => sign('go', note.trim())}>
                Confirm override GO
              </Button>
            </div>
          </div>
        )}
        {err && <p className="mt-1.5 text-[11px] text-fail">{err}</p>}
      </Panel>

      {/* readiness */}
      <Panel className="overflow-hidden">
        <SectionTitle
          action={readiness && <Chip tone={isGo ? 'good' : 'danger'}>{isGo ? 'GO' : 'NO-GO'}</Chip>}>
          Readiness
        </SectionTitle>
        <div className="px-4 py-2">
          {(readiness?.criteria ?? []).length === 0 && (
            <p className="text-[11.5px] text-ink-3">No exit criteria set. Say “exit criteria: pass rate ≥ 95%, 0 open blockers” in chat.</p>
          )}
          {(readiness?.criteria ?? []).map((c, i) => (
            <div key={i} className="flex items-baseline gap-2 border-b border-line/40 py-1.5 text-[12px] last:border-0">
              {c.met ? <Check size={12} className="shrink-0 text-pass" /> : <X size={12} className="shrink-0 text-fail" />}
              <span className="min-w-0 flex-1 text-ink-2">{c.metric} {c.op} {c.target}</span>
              <span className={`mono tabular-nums ${c.met ? 'text-ink-3' : 'text-fail'}`}>{String(c.actual)}</span>
            </div>
          ))}
        </div>
      </Panel>

      {/* metrics */}
      {metrics && (
        <Panel className="overflow-hidden">
          <SectionTitle>Metrics</SectionTitle>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 px-4 py-3 text-[12px] lg:grid-cols-4">
            <Stat label="Execution" value={pct(metrics.execution_progress)} />
            <Stat label="Pass rate" value={pct(metrics.pass_rate)} />
            <Stat label="Req coverage" value={pct(metrics.requirement_coverage)} />
            <Stat label="P1 coverage" value={pct(metrics.p1_requirement_coverage)} />
            <Stat label="Automation" value={pct(metrics.automation_ratio)} />
            <Stat label="Flaky" value={pct(metrics.flaky_rate)} />
            <Stat label="Open blockers" value={String(metrics.open_blockers)} />
            <Stat label="Defect density" value={String(metrics.defect_density)} />
          </div>
        </Panel>
      )}

      {/* cycle configuration matrix */}
      <Panel className="overflow-hidden">
        <SectionTitle hint="one cell per plan × configuration">Cycles</SectionTitle>
        <div className="px-4 py-3">
          {cycles.length === 0 && (
            <p className="text-[11.5px] text-ink-3">No cycles yet. Plan and start them in chat (“plan regression for {m.version} on chromium”, then “start cycle”).</p>
          )}
          <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-4">
            {cycles.map((c) => (
              <div key={c.id} className="rounded-lg border border-line bg-surface-2 p-2">
                <div className="flex items-center gap-1.5">
                  <span className="mono text-[11px] text-ink">{c.configuration?.browser}/{c.configuration?.viewport}</span>
                  <div className="flex-1" />
                  <Chip tone={c.status === 'complete' ? 'good' : 'neutral'}>{c.status}</Chip>
                </div>
                <div className="mt-1.5 text-[10.5px] text-ink-3">
                  {c.counters?.executed ?? 0}/{c.counters?.planned ?? 0} executed · {c.case_count} case(s)
                </div>
              </div>
            ))}
          </div>
        </div>
      </Panel>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-ink-3">{label}</span>
      <span className="mono tabular-nums text-ink">{value}</span>
    </div>
  );
}
