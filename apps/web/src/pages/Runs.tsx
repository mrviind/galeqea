import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import {
  CalendarClock, History, Layers, Pause, Play, Plus, Trash2, X,
} from 'lucide-react';
import { api } from '../lib/api';
import type { RunSummary } from '../lib/api';
import { duration, relative } from '../lib/format';
import { useApp, useEvents } from '../state';
import { Button, Chip, Empty, Panel, SectionTitle, StatusPill } from '../components/primitives';

const TABS = [
  { id: 'history', label: 'History', icon: History },
  { id: 'suites', label: 'Suites', icon: Layers },
  { id: 'schedules', label: 'Schedules', icon: CalendarClock },
] as const;

export default function Runs() {
  const [tab, setTab] = useState<(typeof TABS)[number]['id']>('history');
  return (
    <div className="space-y-3 p-3">
      <div className="flex gap-1">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={clsx(
              'flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] transition',
              tab === id
                ? 'border-accent/30 bg-accent/10 text-accent'
                : 'border-line bg-surface-2 text-ink-3 hover:text-ink-2',
            )}
          >
            <Icon size={12} /> {label}
          </button>
        ))}
      </div>
      {tab === 'history' && <HistoryTab />}
      {tab === 'suites' && <SuitesTab />}
      {tab === 'schedules' && <SchedulesTab />}
    </div>
  );
}

// --------------------------------------------------------------------------- //
function HistoryTab() {
  const { project } = useApp();
  const navigate = useNavigate();
  const [runs, setRuns] = useState<RunSummary[]>([]);

  const load = useCallback(() => {
    if (!project) return;
    api.get<RunSummary[]>(`/api/projects/${project.id}/runs?limit=100`).then(setRuns).catch(() => {});
  }, [project]);

  useEffect(load, [load]);
  useEvents(['run.queued', 'run.finished', 'run.started'], load);

  return (
    <Panel className="overflow-hidden">
      <SectionTitle hint={`${runs.length} run(s)`}>Run history</SectionTitle>
      {runs.length === 0 && <Empty title="No runs yet" body="Runs appear here the moment one starts." />}
      <div className="border-t border-line">
        {runs.map((run) => {
          const totals = run.totals ?? {};
          return (
            <button
              key={run.id}
              onClick={() => navigate(`/runs/${run.id}`)}
              className="flex w-full items-center gap-3 border-b border-line/60 px-4 py-2.5 text-left
                         transition last:border-0 hover:bg-surface-2"
            >
              <StatusPill status={run.status} live={run.status === 'running'} />
              <span className="mono w-10 shrink-0 text-ink-3">#{run.number}</span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[12.5px] text-ink">{run.title}</p>
                <p className="truncate text-[11px] text-ink-3">
                  {run.headline || `${run.trigger} · ${run.environment}`}
                </p>
              </div>
              <div className="mono flex shrink-0 gap-2 text-[11px]">
                {totals.passed ? <span className="text-pass">{totals.passed}✓</span> : null}
                {totals.failed ? <span className="text-fail">{totals.failed}✗</span> : null}
                {totals.needs_review ? <span className="text-review">{totals.needs_review}?</span> : null}
              </div>
              <span className="w-14 shrink-0 text-right text-[11px] text-ink-3">{duration(run.duration_ms)}</span>
              <span className="w-16 shrink-0 text-right text-[11px] text-ink-3">{relative(run.created_at)}</span>
            </button>
          );
        })}
      </div>
    </Panel>
  );
}

// --------------------------------------------------------------------------- //
function SuitesTab() {
  const { project } = useApp();
  const navigate = useNavigate();
  const [suites, setSuites] = useState<any[]>([]);
  const [preview, setPreview] = useState<Record<string, any>>({});
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({ name: '', kind: 'dynamic', query: '' });
  const [error, setError] = useState('');

  const load = useCallback(() => {
    if (!project) return;
    api.get<any[]>(`/api/projects/${project.id}/suites`).then(setSuites).catch(() => {});
  }, [project]);
  useEffect(load, [load]);

  const create = async () => {
    if (!project || !draft.name.trim()) return;
    setError('');
    try {
      await api.post(`/api/projects/${project.id}/suites`, {
        name: draft.name,
        kind: draft.kind,
        query: draft.kind === 'dynamic' ? { text: draft.query } : {},
      });
      setDraft({ name: '', kind: 'dynamic', query: '' });
      setCreating(false);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'could not create that suite');
    }
  };

  const showPreview = async (id: string) => {
    if (!project) return;
    setPreview({ ...preview, [id]: await api.post(`/api/projects/${project.id}/suites/${id}/preview`) });
  };

  const run = async (id: string) => {
    if (!project) return;
    setError('');
    try {
      const res = await api.post<any>(`/api/projects/${project.id}/suites/${id}/run`, {});
      navigate(`/runs/${res.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'could not start that run');
    }
  };

  const remove = async (id: string) => {
    if (!project) return;
    setError('');
    try {
      await api.del(`/api/projects/${project.id}/suites/${id}`);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'could not delete that suite');
    }
  };

  return (
    <Panel className="overflow-hidden">
      <SectionTitle
        hint="a dynamic suite is a saved query, resolved at run time"
        action={
          <Button size="sm" variant={creating ? 'ghost' : 'primary'} onClick={() => setCreating(!creating)}>
            {creating ? <X size={11} /> : <Plus size={11} />} {creating ? 'Cancel' : 'New suite'}
          </Button>
        }
      >
        Suites
      </SectionTitle>

      {error && (
        <p className="mx-4 mb-2 rounded-lg border border-fail/30 bg-fail/[0.07] px-2.5 py-1.5 text-[11.5px] text-fail">
          {error}
        </p>
      )}

      {creating && (
        <div className="mx-4 mb-3 space-y-2 rounded-lg border border-line bg-surface-2 p-3">
          <input
            autoFocus
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            placeholder="Suite name, e.g. Checkout regression"
            className="w-full rounded-lg border border-line bg-canvas px-2.5 py-1.5 text-[12px]
                       outline-none placeholder:text-ink-3 focus:border-accent"
          />
          <div className="flex gap-2">
            <select
              value={draft.kind}
              onChange={(e) => setDraft({ ...draft, kind: e.target.value })}
              className="rounded-lg border border-line bg-canvas px-2.5 py-1.5 text-[12px] outline-none focus:border-accent"
            >
              <option value="dynamic">dynamic — a saved query</option>
              <option value="static">static — a fixed list</option>
            </select>
            {draft.kind === 'dynamic' && (
              <input
                value={draft.query}
                onChange={(e) => setDraft({ ...draft, query: e.target.value })}
                placeholder="matches title or tag, e.g. checkout"
                className="min-w-0 flex-1 rounded-lg border border-line bg-canvas px-2.5 py-1.5 text-[12px]
                           outline-none placeholder:text-ink-3 focus:border-accent"
              />
            )}
          </div>
          <Button size="sm" variant="primary" onClick={create} disabled={!draft.name.trim()}>
            Create suite
          </Button>
        </div>
      )}

      <div className="border-t border-line">
        {suites.length === 0 && (
          <Empty
            icon={<Layers size={20} />}
            title="No suites yet"
            body="A suite groups tests so they can be run and scheduled together. Dynamic suites re-resolve their query on every run, so new tests join automatically."
          />
        )}
        {suites.map((suite) => (
          <div key={suite.id} className="border-b border-line/60 px-4 py-3 last:border-0">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-medium text-ink">{suite.name}</span>
              <Chip tone={suite.kind === 'dynamic' ? 'brand' : 'neutral'}>{suite.kind}</Chip>
              {suite.kind === 'dynamic' && suite.query?.text && (
                <code className="mono rounded bg-surface-3 px-1.5 py-0.5 text-[10.5px] text-ink-3">
                  {suite.query.text}
                </code>
              )}
              <span className="ml-auto text-[10.5px] text-ink-3">
                {suite.kind === 'dynamic' ? 'resolved at run time' : `${suite.size} test(s)`}
              </span>
            </div>

            {preview[suite.id] && (
              <div className="mt-2 space-y-0.5">
                <p className="text-[10.5px] text-ink-3">
                  Would run {preview[suite.id].count} test(s) right now:
                </p>
                {preview[suite.id].tests.slice(0, 6).map((t: any) => (
                  <p key={t.key} className="mono text-[10.5px] text-ink-3">{t.key} · {t.title}</p>
                ))}
              </div>
            )}

            <div className="mt-2 flex gap-1.5">
              <Button size="sm" variant="primary" onClick={() => run(suite.id)}>
                <Play size={11} /> Run
              </Button>
              <Button size="sm" variant="ghost" onClick={() => showPreview(suite.id)}>
                What would this run?
              </Button>
              <Button size="sm" variant="subtle" onClick={() => remove(suite.id)}>
                <Trash2 size={11} />
              </Button>
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

// --------------------------------------------------------------------------- //
function SchedulesTab() {
  const { project } = useApp();
  const navigate = useNavigate();
  const [schedules, setSchedules] = useState<any[]>([]);
  const [suites, setSuites] = useState<any[]>([]);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({ name: '', cron: '0 2 * * *', suite_id: '' });
  const [cronCheck, setCronCheck] = useState<any>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    if (!project) return;
    const [s, su] = await Promise.all([
      api.get<any[]>(`/api/projects/${project.id}/schedules`),
      api.get<any[]>(`/api/projects/${project.id}/suites`),
    ]);
    setSchedules(s); setSuites(su);
  }, [project]);
  useEffect(() => { void load(); }, [load]);

  // Explain the cron before it is saved: nobody should have to guess whether
  // '0 18 * * 1' is what they meant.
  useEffect(() => {
    if (!project || !draft.cron.trim()) { setCronCheck(null); return; }
    const t = setTimeout(async () => {
      setCronCheck(await api.post(`/api/projects/${project.id}/schedules/preview-cron`, { cron: draft.cron }));
    }, 250);
    return () => clearTimeout(t);
  }, [draft.cron, project]);

  const create = async () => {
    if (!project) return;
    setError('');
    try {
      await api.post(`/api/projects/${project.id}/schedules`, {
        name: draft.name, cron: draft.cron, suite_id: draft.suite_id || null,
      });
      setDraft({ name: '', cron: '0 2 * * *', suite_id: '' });
      setCreating(false);
      void load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'could not create that schedule');
    }
  };

  const toggle = async (s: any) => {
    if (!project) return;
    await api.patch(`/api/projects/${project.id}/schedules/${s.id}`, { enabled: !s.enabled });
    void load();
  };

  const fire = async (s: any) => {
    if (!project) return;
    const res = await api.post<any>(`/api/projects/${project.id}/schedules/${s.id}/run-now`, {});
    navigate(`/runs/${res.id}`);
  };

  const remove = async (s: any) => {
    if (!project) return;
    await api.del(`/api/projects/${project.id}/schedules/${s.id}`);
    void load();
  };

  return (
    <Panel className="overflow-hidden">
      <SectionTitle
        hint="times are UTC"
        action={
          <Button size="sm" variant={creating ? 'ghost' : 'primary'} onClick={() => setCreating(!creating)}>
            {creating ? <X size={11} /> : <Plus size={11} />} {creating ? 'Cancel' : 'New schedule'}
          </Button>
        }
      >
        Schedules
      </SectionTitle>

      {error && (
        <p className="mx-4 mb-2 rounded-lg border border-fail/30 bg-fail/[0.07] px-2.5 py-1.5 text-[11.5px] text-fail">
          {error}
        </p>
      )}

      {creating && (
        <div className="mx-4 mb-3 space-y-2 rounded-lg border border-line bg-surface-2 p-3">
          <input
            autoFocus
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            placeholder="Schedule name, e.g. Nightly regression"
            className="w-full rounded-lg border border-line bg-canvas px-2.5 py-1.5 text-[12px]
                       outline-none placeholder:text-ink-3 focus:border-accent"
          />
          <div className="flex gap-2">
            <input
              value={draft.cron}
              onChange={(e) => setDraft({ ...draft, cron: e.target.value })}
              placeholder="0 2 * * *"
              className="mono w-40 rounded-lg border border-line bg-canvas px-2.5 py-1.5 text-[12px]
                         outline-none placeholder:text-ink-3 focus:border-accent"
            />
            <select
              value={draft.suite_id}
              onChange={(e) => setDraft({ ...draft, suite_id: e.target.value })}
              className="min-w-0 flex-1 rounded-lg border border-line bg-canvas px-2.5 py-1.5 text-[12px]
                         outline-none focus:border-accent"
            >
              <option value="">all approved automated tests</option>
              {suites.map((s) => <option key={s.id} value={s.id}>suite: {s.name}</option>)}
            </select>
          </div>
          {cronCheck && (
            <p className={clsx('text-[11px]', cronCheck.valid ? 'text-ink-2' : 'text-fail')}>
              {cronCheck.valid ? `Runs ${cronCheck.description}.` : cronCheck.error}
            </p>
          )}
          <Button
            size="sm" variant="primary" onClick={create}
            disabled={!draft.name.trim() || !cronCheck?.valid}
          >
            Create schedule
          </Button>
        </div>
      )}

      <div className="border-t border-line">
        {schedules.length === 0 && (
          <Empty
            icon={<CalendarClock size={20} />}
            title="Nothing scheduled"
            body='Create one here, or ask the assistant: "schedule regression nightly at 2am".'
          />
        )}
        {schedules.map((s) => (
          <div key={s.id} className="flex items-center gap-3 border-b border-line/60 px-4 py-3 last:border-0">
            <span className={clsx('dot h-2 w-2 shrink-0', s.enabled ? 'bg-pass' : 'bg-ink-3')} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-[12.5px] font-medium text-ink">{s.name}</span>
                <code className="mono rounded bg-surface-3 px-1.5 py-0.5 text-[10.5px] text-ink-3">{s.cron}</code>
                {!s.enabled && <Chip>paused</Chip>}
              </div>
              <p className="mt-0.5 text-[11px] text-ink-3">
                {s.description}
                {s.next_fire_at && s.enabled && <> · next {relative(s.next_fire_at)}</>}
                {s.last_fired_at && <> · last fired {relative(s.last_fired_at)}</>}
              </p>
            </div>
            <div className="flex shrink-0 gap-1.5">
              <Button size="sm" variant="ghost" onClick={() => fire(s)} title="Fire now">
                <Play size={11} /> Run now
              </Button>
              <Button size="sm" variant="ghost" onClick={() => toggle(s)}>
                {s.enabled ? <><Pause size={11} /> Pause</> : <><Play size={11} /> Resume</>}
              </Button>
              <Button size="sm" variant="subtle" onClick={() => remove(s)}>
                <Trash2 size={11} />
              </Button>
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
