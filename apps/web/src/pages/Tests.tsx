import { useCallback, useEffect, useMemo, useState } from 'react';
import clsx from 'clsx';
import {
  Check, ChevronRight, Code2, Compass, FileCode2, Play, ShieldOff, TestTube2, X,
} from 'lucide-react';
import { api } from '../lib/api';
import type { TestCase } from '../lib/api';
import { RISK_COLOR } from '../lib/format';
import { useApp } from '../state';
import { Button, Chip, Empty, Panel, SectionTitle, Spinner } from '../components/primitives';

const CATEGORY_ICON = {
  automated: <Code2 size={12} />,
  manual: <TestTube2 size={12} />,
  exploratory: <Compass size={12} />,
};

/**
 * The human-in-the-loop review board.
 *
 * Reviewers approve *rationale* as much as steps, so the rationale is given as
 * much room as the steps are — a proposal you cannot judge is a proposal you
 * will rubber-stamp.
 */
export default function Tests() {
  const { project, refreshOverview } = useApp();
  const [tests, setTests] = useState<TestCase[]>([]);
  const [filter, setFilter] = useState<'proposed' | 'approved' | 'all'>('proposed');
  const [selected, setSelected] = useState<TestCase | null>(null);
  const [busy, setBusy] = useState(false);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [exported, setExported] = useState<{ filename: string; code: string } | null>(null);

  const load = useCallback(async () => {
    if (!project) return;
    const query = filter === 'all' ? '' : `?status=${filter}`;
    const body = await api.get<{ tests: TestCase[] }>(`/api/projects/${project.id}/tests${query}`);
    setTests(body.tests);
    setSelected((prev) => body.tests.find((t) => t.id === prev?.id) ?? body.tests[0] ?? null);
  }, [project, filter]);

  useEffect(() => { void load(); }, [load]);

  const review = async (decision: 'approve' | 'reject', test: TestCase) => {
    if (!project) return;
    setBusy(true);
    try {
      await api.post(`/api/projects/${project.id}/tests/${test.id}/review`, { decision });
      await load();
      void refreshOverview();
    } finally { setBusy(false); }
  };

  const bulk = async (decision: 'approve' | 'reject') => {
    if (!project || checked.size === 0) return;
    setBusy(true);
    try {
      await api.post(`/api/projects/${project.id}/tests/bulk-review`, {
        decision, test_ids: [...checked],
      });
      setChecked(new Set());
      await load();
      void refreshOverview();
    } finally { setBusy(false); }
  };

  const runSelected = async () => {
    if (!project || !selected) return;
    await api.post(`/api/projects/${project.id}/runs`, {
      selection: { test_ids: [selected.id] }, title: `Run ${selected.key}`,
    });
  };

  const exportTest = async (target: string) => {
    if (!project || !selected) return;
    setExported(await api.get(`/api/projects/${project.id}/tests/${selected.id}/export?target=${target}`));
  };

  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    tests.forEach((t) => { out[t.category] = (out[t.category] ?? 0) + 1; });
    return out;
  }, [tests]);

  return (
    <div className="grid h-full gap-3 p-3 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
      <Panel className="flex min-h-0 flex-col overflow-hidden">
        <SectionTitle hint={Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(' · ')}>
          Test cases
        </SectionTitle>

        <div className="flex gap-1 px-3 pb-2">
          {(['proposed', 'approved', 'all'] as const).map((value) => (
            <button
              key={value}
              onClick={() => setFilter(value)}
              className={clsx(
                'px-2 py-1 text-[11.5px] transition',
                filter === value ? 'bg-surface-3 text-ink' : 'text-ink-3 hover:text-ink-2',
              )}
            >
              {value}
            </button>
          ))}
          {checked.size > 0 && (
            <div className="ml-auto flex gap-1">
              <Button size="sm" variant="primary" onClick={() => bulk('approve')} disabled={busy}>
                <Check size={11} /> Approve {checked.size}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => bulk('reject')} disabled={busy}>
                <X size={11} />
              </Button>
            </div>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto border-t border-line">
          {tests.length === 0 && (
            <Empty
              title={filter === 'proposed' ? 'Nothing awaiting review' : 'No test cases'}
              body="Upload a requirement document on the Requirements page, then generate proposals."
            />
          )}
          {tests.map((test) => (
            <div
              key={test.id}
              className={clsx(
                'flex items-start gap-2 border-b border-line/60 px-3 py-2 transition last:border-0',
                selected?.id === test.id ? 'bg-surface-2' : 'hover:bg-surface-2/60',
              )}
            >
              {test.status === 'proposed' && (
                <input
                  type="checkbox"
                  checked={checked.has(test.id)}
                  onChange={(e) => {
                    const next = new Set(checked);
                    e.target.checked ? next.add(test.id) : next.delete(test.id);
                    setChecked(next);
                  }}
                  className="mt-1 accent-ink"
                />
              )}
              <button onClick={() => setSelected(test)} className="min-w-0 flex-1 text-left">
                <div className="flex items-center gap-1.5">
                  <span className="mono shrink-0 text-[10.5px] text-ink-3">{test.key}</span>
                  <span className={clsx('border px-1 text-[9.5px]', RISK_COLOR[test.risk] ?? RISK_COLOR.medium)}>
                    {test.risk}
                  </span>
                  {test.quarantined && <Chip tone="warn"><ShieldOff size={9} /> quarantined</Chip>}
                </div>
                <p className="mt-0.5 truncate text-[12.5px] text-ink">{test.title}</p>
                <div className="mt-1 flex items-center gap-1.5 text-[10.5px] text-ink-3">
                  <span className="flex items-center gap-1">
                    {CATEGORY_ICON[test.category]} {test.category}
                  </span>
                  <span>· {test.steps.length} steps</span>
                  {test.requirement_refs.length > 0 && <span>· {test.requirement_refs.join(', ')}</span>}
                </div>
              </button>
              <ChevronRight size={12} className="mt-1 shrink-0 text-ink-3" />
            </div>
          ))}
        </div>
      </Panel>

      {/* --- detail ------------------------------------------------------- */}
      {selected ? (
        <Panel className="flex min-h-0 flex-col overflow-hidden">
          <div className="flex items-start gap-3 border-b border-line p-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="mono text-[11px] text-ink-3">{selected.key}</span>
                <Chip tone={selected.status === 'approved' ? 'good' : 'brand'}>{selected.status}</Chip>
                <Chip>{selected.category}</Chip>
                <Chip>{selected.priority}</Chip>
              </div>
              <h1 className="mt-1 text-[14px] font-semibold leading-snug">{selected.title}</h1>
            </div>
            <div className="flex shrink-0 gap-1.5">
              {selected.status === 'proposed' && (
                <>
                  <Button size="sm" variant="primary" onClick={() => review('approve', selected)} disabled={busy}>
                    <Check size={11} /> Approve
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => review('reject', selected)} disabled={busy}>
                    <X size={11} /> Reject
                  </Button>
                </>
              )}
              {selected.status === 'approved' && selected.category === 'automated' && (
                <Button size="sm" variant="primary" onClick={runSelected}><Play size={11} /> Run</Button>
              )}
            </div>
          </div>

          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
            {selected.rationale && (
              <section>
                <h2 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-3">
                  Why this test exists
                </h2>
                <p className="rounded-lg border border-line bg-surface-2 p-2.5 text-[12.5px] leading-relaxed text-ink-2">
                  {selected.rationale}
                </p>
              </section>
            )}

            {selected.charter && (
              <section>
                <h2 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-3">
                  Exploratory charter
                </h2>
                <p className="rounded-lg border border-review/25 bg-review/[0.06] p-2.5 text-[12.5px] leading-relaxed text-ink-2">
                  {selected.charter}
                </p>
              </section>
            )}

            {selected.preconditions?.length > 0 && (
              <section>
                <h2 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-3">Preconditions</h2>
                <ul className="space-y-0.5">
                  {selected.preconditions.map((p, i) => (
                    <li key={i} className="text-[12px] text-ink-2">· {p}</li>
                  ))}
                </ul>
              </section>
            )}

            <section>
              <h2 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-3">
                Steps ({selected.steps.length})
              </h2>
              <ol className="space-y-px">
                {selected.steps.map((step) => (
                  <li key={step.index} className="rounded-md flex items-baseline gap-2 px-1.5 py-1 hover:bg-surface-2">
                    <span className="mono w-5 shrink-0 text-right text-[10px] text-ink-3">{step.index}</span>
                    <Chip className="shrink-0">{step.action}</Chip>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[12px] text-ink-2">{step.intent}</span>
                      {step.expected && <span className="block text-[11px] text-ink-3">expects: {step.expected}</span>}
                      {step.target?.ladder?.length > 0 && (
                        <span className="mono block truncate text-[10px] text-ink-3">
                          {step.target.ladder.map((r: any) => r.kind === 'role' ? `role=${r.role}"${r.name ?? ''}"` : `${r.kind}=${r.value}`).join(' →  ')}
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ol>
            </section>

            <section>
              <h2 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-3">
                Provenance
              </h2>
              <pre className="rounded-md mono overflow-auto border border-line bg-surface-2 p-2.5 text-[10.5px] text-ink-3">
                {JSON.stringify(selected.provenance, null, 2)}
              </pre>
            </section>

            <section>
              <div className="mb-1 flex items-center gap-2">
                <h2 className="text-[11px] font-medium uppercase tracking-wide text-ink-3">Export</h2>
                <span className="text-[10.5px] text-ink-3">runs anywhere, with no QE Agent dependency</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {['playwright', 'playwright_python', 'robot', 'cucumber'].map((target) => (
                  <Button key={target} size="sm" variant="ghost" onClick={() => exportTest(target)}>
                    <FileCode2 size={11} /> {target}
                  </Button>
                ))}
              </div>
              {exported && (
                <pre className="rounded-md mono mt-2 max-h-64 overflow-auto border border-line bg-canvas p-3 text-[11px] leading-relaxed text-ink-2">
                  {exported.code}
                </pre>
              )}
            </section>
          </div>
        </Panel>
      ) : (
        <Panel><Empty title="Select a test" body="Its rationale, steps, locator ladder and provenance appear here." /></Panel>
      )}
    </div>
  );
}
