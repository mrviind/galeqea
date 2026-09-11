import { useCallback, useEffect, useMemo, useState } from 'react';
import clsx from 'clsx';
import {
  Check, Download, ChevronRight, Code2, Compass, FileCode2, MapPin, Pencil, Play, Sparkles,
  ShieldOff, TestTube2, X,
} from 'lucide-react';
import { api } from '../lib/api';
import type { CoveredRule, TestCase } from '../lib/api';
import { RISK_COLOR } from '../lib/format';
import { useApp } from '../state';
import { Button, Chip, Empty, Panel, SectionTitle, Spinner } from '../components/primitives';

const CATEGORY_ICON = {
  automated: <Code2 size={12} />,
  manual: <TestTube2 size={12} />,
  exploratory: <Compass size={12} />,
};

/** Flatten inline markdown to plain text (for titles). */
function stripMd(text: string): string {
  return (text || '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/(\*\*|__|~~|\*|_|`)(.+?)\1/g, '$2')
    .replace(/[*_`~]/g, '');
}

/** Render a tiny, safe subset of inline markdown (bold/italic/code) for rationale. */
function InlineMd({ text }: { text: string }) {
  const parts = (text || '').split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g);
  return (
    <>
      {parts.map((p, i) => {
        if (/^\*\*[^*]+\*\*$/.test(p)) return <strong key={i}>{p.slice(2, -2)}</strong>;
        if (/^`[^`]+`$/.test(p)) return <code key={i} className="mono text-[0.95em]">{p.slice(1, -1)}</code>;
        if (/^\*[^*]+\*$/.test(p)) return <em key={i}>{p.slice(1, -1)}</em>;
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}

/**
 * The human-in-the-loop review board.
 *
 * Reviewers approve *rationale* as much as steps, so the rationale is given as
 * much room as the steps are. A proposal you cannot judge is a proposal you
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
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [coveredRules, setCoveredRules] = useState<CoveredRule[]>([]);
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState('');
  const [editRationale, setEditRationale] = useState('');
  const [instruction, setInstruction] = useState('');
  const [regenBusy, setRegenBusy] = useState(false);
  const [regenNote, setRegenNote] = useState('');

  const load = useCallback(async () => {
    if (!project) return;
    const query = filter === 'all' ? '' : `?status=${filter}`;
    const body = await api.get<{ tests: TestCase[] }>(`/api/projects/${project.id}/tests${query}`);
    setTests(body.tests);
    setSelected((prev) => body.tests.find((t) => t.id === prev?.id) ?? body.tests[0] ?? null);
  }, [project, filter]);

  useEffect(() => { void load(); }, [load]);

  // Pull the covered rules (with their source anchors) for the selected case, so the
  // review board can show which rule the test exercises and where it was written.
  useEffect(() => {
    setEditing(false); setRegenNote('');
    if (!project || !selected) { setCoveredRules([]); return; }
    let live = true;
    void api.get<{ covered_rules: CoveredRule[] }>(`/api/projects/${project.id}/tests/${selected.id}`)
      .then((d) => { if (live) setCoveredRules(d.covered_rules || []); })
      .catch(() => { if (live) setCoveredRules([]); });
    return () => { live = false; };
  }, [project, selected?.id]);

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

  const startEdit = () => {
    if (!selected) return;
    setEditTitle(stripMd(selected.title)); setEditRationale(selected.rationale || '');
    setEditing(true);
  };

  const saveEdit = async (thenApprove: boolean) => {
    if (!project || !selected) return;
    setBusy(true);
    try {
      await api.post(`/api/projects/${project.id}/tests/${selected.id}/review`, {
        decision: thenApprove ? 'approve' : 'save',
        edits: { title: editTitle, rationale: editRationale },
      });
      setEditing(false);
      await load();
      void refreshOverview();
    } finally { setBusy(false); }
  };

  const regenerate = async () => {
    if (!project || !selected || !instruction.trim()) return;
    setRegenBusy(true); setRegenNote('');
    try {
      const res = await api.post<{ ok: boolean; note?: string }>(
        `/api/projects/${project.id}/tests/${selected.id}/regenerate`,
        { instruction: instruction.trim() });
      if (res.ok) { setInstruction(''); await load(); }
      else { setRegenNote(res.note || 'could not regenerate'); }
    } finally { setRegenBusy(false); }
  };

  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    tests.forEach((t) => { out[t.category] = (out[t.category] ?? 0) + 1; });
    return out;
  }, [tests]);

  // Group by Golden Path suite (its type) so 62 cases read as a few named suites,
  // not a flat wall. Search and a type filter narrow within that.
  const TYPE_LABEL: Record<string, string> = {
    functional: 'Functional', forms: 'Forms', links: 'Links', api: 'API', a11y: 'Accessibility',
    perf: 'Performance', visual: 'Visual', responsive: 'Responsive', cross_browser: 'Cross-browser',
    security: 'Security', seo: 'SEO', resilience: 'Resilience', data_driven: 'Data-driven',
    exploratory: 'Exploratory', manual: 'Manual', login: 'Login',
  };
  const testType = (t: TestCase): string => {
    const p = t.provenance?.type as string | undefined;
    if (p) return p;
    const tag = (t.tags || []).find((x) => x !== 'golden-path' && x in TYPE_LABEL);
    return tag ?? '';
  };
  // Group by *origin*, not just golden-path type, so requirement-derived cases sit
  // under their source document rather than a generic "Functional" bucket (WO#9-D).
  const groupName = (t: TestCase): string => {
    const origin = t.provenance?.origin as string | undefined;
    const authorKind = t.provenance?.author_kind as string | undefined;
    if (t.category === 'exploratory') return 'Exploratory';
    if (authorKind === 'import' || origin === 'import') {
      return `Imported · ${(t.provenance?.format as string) || 'external'}`;
    }
    const ty = testType(t);
    const isGolden = (t.tags || []).includes('golden-path') || !!t.provenance?.type;
    if (isGolden && ty) return `Golden Path · ${TYPE_LABEL[ty] ?? ty}`;
    if ((t.requirement_refs?.length ?? 0) > 0 || (t.covers?.length ?? 0) > 0) {
      const doc = t.provenance?.doc_title as string | undefined;
      return doc ? `Requirements · ${doc}` : 'Requirements';
    }
    return ty ? `Golden Path · ${TYPE_LABEL[ty] ?? ty}` : 'Other tests';
  };
  const allTypes = useMemo(
    () => [...new Set(tests.map(testType).filter(Boolean))].sort(),
    [tests]);
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return tests.filter((t) => {
      if (typeFilter !== 'all' && testType(t) !== typeFilter) return false;
      if (!q) return true;
      const unit = (t.provenance?.unit as string) || '';
      return t.title.toLowerCase().includes(q) || t.key.toLowerCase().includes(q)
        || unit.toLowerCase().includes(q) || (t.tags || []).some((x) => x.toLowerCase().includes(q));
    });
  }, [tests, search, typeFilter]);
  const groups = useMemo(() => {
    const m = new Map<string, TestCase[]>();
    for (const t of filtered) (m.get(groupName(t)) ?? m.set(groupName(t), []).get(groupName(t))!).push(t);
    return [...m.entries()].sort((a, b) => (a[0] === 'Other tests' ? 1 : b[0] === 'Other tests' ? -1 : a[0].localeCompare(b[0])));
  }, [filtered]);

  return (
    <div className="grid h-full gap-3 p-3 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
      <Panel className="flex min-h-0 flex-col overflow-hidden">
        <SectionTitle hint={Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(' · ')}>
          Test cases
        </SectionTitle>

        <div className="flex items-center gap-1 px-3 pb-2">
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

        {tests.length > 0 && (
          <div className="flex items-center gap-1.5 px-3 pb-2">
            <input
              value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search title, key, page, tag…"
              className="min-w-0 flex-1 rounded border border-line bg-surface-2 px-2 py-1 text-[11.5px] text-ink outline-none focus:border-accent"
            />
            <select aria-label="Filter tests by type"
              value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}
              className="rounded border border-line bg-surface-2 px-1.5 py-1 text-[11px] text-ink-2 outline-none focus:border-accent"
            >
              <option value="all">all types</option>
              {allTypes.map((ty) => <option key={ty} value={ty}>{TYPE_LABEL[ty] ?? ty}</option>)}
            </select>
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto border-t border-line">
          {tests.length === 0 && (
            <Empty
              title={filter === 'proposed' ? 'Nothing awaiting review' : 'No test cases'}
              body="Upload a requirement document on the Requirements page, or test a URL from the chat, then generate proposals."
            />
          )}
          {tests.length > 0 && filtered.length === 0 && (
            <Empty title="No matches" body="Nothing matches that search or type filter." />
          )}
          {groups.map(([name, groupTests]) => (
            <div key={name}>
              <button
                onClick={() => setCollapsed((c) => { const n = new Set(c); n.has(name) ? n.delete(name) : n.add(name); return n; })}
                className="sticky top-0 z-10 flex w-full items-center gap-1.5 border-b border-line bg-surface px-3 py-1.5 text-left text-[11px] font-medium text-ink-2 hover:bg-surface-2"
              >
                <ChevronRight size={11} className={clsx('shrink-0 transition', !collapsed.has(name) && 'rotate-90')} />
                <span className="min-w-0 flex-1 truncate">{name}</span>
                <span className="mono shrink-0 text-ink-3">{groupTests.length}</span>
              </button>
              {!collapsed.has(name) && groupTests.map((test) => (
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
                  aria-label={`Select ${test.key} for bulk review`}
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
                <p className="mt-0.5 truncate text-[12.5px] text-ink">{stripMd(test.title)}</p>
                <div className="mt-1 flex items-center gap-1.5 text-[10.5px] text-ink-3">
                  <span className="flex items-center gap-1">
                    {CATEGORY_ICON[test.category]} {test.category}
                  </span>
                  <span>· {test.steps.length} steps</span>
                  {test.requirement_refs.length > 0 && <span>· {test.requirement_refs.join(', ')}</span>}
                  {test.technique && (
                    <span className="rounded bg-accent/10 px-1 text-accent">{test.technique.replace(/_/g, ' ')}</span>
                  )}
                </div>
              </button>
              <ChevronRight size={12} className="mt-1 shrink-0 text-ink-3" />
            </div>
              ))}
            </div>
          ))}
          <button
            onClick={async () => {
              if (!project) return;
              const res = await fetch(`/api/projects/${project.id}/tests/export.xlsx`);
              if (!res.ok) return;
              const url = URL.createObjectURL(await res.blob());
              const a = document.createElement('a'); a.href = url; a.download = 'test-cases.xlsx';
              document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
            }}
            className="mono ml-auto flex items-center gap-1 rounded px-2 py-1 text-[11px] text-ink-3 transition hover:bg-surface-3 hover:text-ink"
            title="Download the test-case register as Excel"
          >
            <Download size={11} /> Excel
          </button>
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
              <h1 className="mt-1 text-[14px] font-semibold leading-snug">{stripMd(selected.title)}</h1>
            </div>
            <div className="flex shrink-0 gap-1.5">
              {selected.status === 'proposed' && (
                <>
                  <Button size="sm" variant="ghost" onClick={startEdit} disabled={busy}>
                    <Pencil size={11} /> Edit
                  </Button>
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
            {editing && (
              <section className="rounded-lg border border-accent/30 bg-accent/[0.05] p-2.5">
                <h2 className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-accent">Edit proposal</h2>
                <input
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  className="rounded-md mb-2 w-full border border-line bg-surface-2 px-2.5 py-1.5 text-[12.5px] outline-none focus:border-accent/40"
                  placeholder="Title"
                />
                <textarea
                  value={editRationale}
                  onChange={(e) => setEditRationale(e.target.value)}
                  rows={3}
                  className="rounded-md w-full resize-y border border-line bg-surface-2 px-2.5 py-1.5 text-[12px] outline-none focus:border-accent/40"
                  placeholder="Why this test exists"
                />
                <div className="mt-2 flex items-center gap-1.5">
                  <Button size="sm" variant="primary" onClick={() => saveEdit(false)} disabled={busy}>
                    <Check size={11} /> Save
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => saveEdit(true)} disabled={busy}>
                    Save &amp; approve
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={busy}>Cancel</Button>
                  <span className="text-[10.5px] text-ink-3">your edits teach the generator this team&apos;s style</span>
                </div>
              </section>
            )}

            {(selected.technique || (selected.covers?.length ?? 0) > 0 || coveredRules.length > 0
              || (selected.assumptions?.length ?? 0) > 0) && (
              <section>
                <h2 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-3">Rule &amp; technique</h2>
                <div className="flex flex-wrap items-center gap-1.5">
                  {selected.technique && (
                    <Chip className="bg-accent/10 text-accent">{selected.technique.replace(/_/g, ' ')}</Chip>
                  )}
                  {(selected.covers || []).map((c) => <Chip key={c}>{c}</Chip>)}
                </div>
                {coveredRules.map((r) => (
                  <div key={r.rule_id} className="rounded-lg mt-1.5 border border-line bg-surface-2 p-2 text-[11.5px]">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Chip>{r.rule_id}</Chip>
                      <span className="text-ink-3">{r.rule_type}</span>
                      {r.technique && <span className="text-ink-3">· {r.technique.replace(/_/g, ' ')}</span>}
                    </div>
                    <p className="mt-1 text-ink-2"><InlineMd text={r.text} /></p>
                    {(r.source_anchor?.heading_path?.length ?? 0) > 0 && (
                      <div className="mt-1 flex items-center gap-1 text-[10.5px] text-ink-3">
                        <MapPin size={10} />
                        {r.source_anchor.heading_path.join(' › ')}
                        {r.source_anchor.page ? ` · p${r.source_anchor.page}` : ''}
                        {r.source_anchor.line ? ` · line ${r.source_anchor.line}` : ''}
                      </div>
                    )}
                  </div>
                ))}
                {(selected.assumptions || []).map((a, i) => (
                  <p key={i} className="rounded-md mt-1.5 border border-review/25 bg-review/[0.06] px-2 py-1 text-[11px] text-ink-2">
                    {a}
                  </p>
                ))}
              </section>
            )}

            {selected.rationale && (
              <section>
                <h2 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-3">
                  Why this test exists
                </h2>
                <p className="rounded-lg border border-line bg-surface-2 p-2.5 text-[12.5px] leading-relaxed text-ink-2">
                  <InlineMd text={selected.rationale} />
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
              <pre tabIndex={0} className="rounded-md mono overflow-auto border border-line bg-surface-2 p-2.5 text-[10.5px] text-ink-3">
                {JSON.stringify(selected.provenance, null, 2)}
              </pre>
            </section>

            {selected.status === 'proposed' && (
              <section>
                <h2 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-3">
                  Regenerate with an instruction
                </h2>
                <div className="flex items-center gap-1.5">
                  <input
                    value={instruction}
                    onChange={(e) => setInstruction(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') void regenerate(); }}
                    placeholder="e.g. make the assertions stronger; add a mobile viewport case"
                    className="rounded-md flex-1 border border-line bg-surface-2 px-2.5 py-1.5 text-[12px] outline-none focus:border-accent/40"
                  />
                  <Button size="sm" variant="ghost" onClick={regenerate} disabled={regenBusy || !instruction.trim()}>
                    {regenBusy ? <Spinner /> : <Sparkles size={11} />} Revise
                  </Button>
                </div>
                {regenNote && <p className="mt-1 text-[11px] text-ink-3">{regenNote}</p>}
              </section>
            )}

            <section>
              <div className="mb-1 flex items-center gap-2">
                <h2 className="text-[11px] font-medium uppercase tracking-wide text-ink-3">Export</h2>
                <span className="text-[10.5px] text-ink-3">runs anywhere, with no GaleQEA dependency</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {['playwright', 'playwright_python', 'robot', 'cucumber'].map((target) => (
                  <Button key={target} size="sm" variant="ghost" onClick={() => exportTest(target)}>
                    <FileCode2 size={11} /> {target}
                  </Button>
                ))}
              </div>
              {exported && (
                <pre tabIndex={0} className="rounded-md mono mt-2 max-h-64 overflow-auto border border-line bg-canvas p-3 text-[11px] leading-relaxed text-ink-2">
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
