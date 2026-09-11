import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import clsx from 'clsx';
import { AlertTriangle, Download, FileUp, HelpCircle, PlayCircle, Sparkles, Upload } from 'lucide-react';
import { api } from '../lib/api';
import type { Coverage } from '../lib/api';
import { RISK_COLOR } from '../lib/format';
import { useApp } from '../state';
import { Button, Chip, Empty, Meter, Panel, SectionTitle, Spinner } from '../components/primitives';
import { BandRow, InfoPopover } from '../components/ui/InfoPopover';

type Band = { label: string; tone: 'pass' | 'flaky' | 'fail' };

/** Coverage and automation are both "more is better" percentages, so the same
 *  three bands apply to each: strong / improving / needs work. */
function bandFor(pct: number): Band {
  if (pct >= 80) return { label: 'Strong', tone: 'pass' };
  if (pct >= 40) return { label: 'Improving', tone: 'flaky' };
  return { label: 'Needs work', tone: 'fail' };
}

const CHIP_TONE: Record<Band['tone'], 'good' | 'warn' | 'danger'> = {
  pass: 'good', flaky: 'warn', fail: 'danger',
};

const RISK_TIER_STYLE: Record<string, string> = {
  critical: 'border-fail/30 bg-fail/[0.06]',
  high: 'border-flaky/30 bg-flaky/[0.06]',
  medium: 'border-line-strong bg-surface-3',
  low: 'border-line bg-surface-3',
};

async function downloadBlob(url: string, filename: string): Promise<void> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`download failed: ${res.status}`);
  const objectUrl = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}

export default function Requirements() {
  const { project, refreshOverview } = useApp();
  const [items, setItems] = useState<any[]>([]);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [matrix, setMatrix] = useState<any[]>([]);
  const [uploading, setUploading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [sampling, setSampling] = useState(false);
  const [floorResult, setFloorResult] = useState<any | null>(null);
  const [notice, setNotice] = useState<string>('');
  const [injection, setInjection] = useState<any | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (!project) return;
    const [reqs, cov, trace] = await Promise.all([
      api.get<any[]>(`/api/projects/${project.id}/requirements`),
      api.get<Coverage>(`/api/projects/${project.id}/requirements/coverage`),
      api.get<{ matrix: any[] }>(`/api/projects/${project.id}/requirements/traceability`),
    ]);
    setItems(reqs); setCoverage(cov); setMatrix(trace.matrix);
  }, [project]);

  useEffect(() => { void load(); }, [load]);

  const upload = async (file: File) => {
    if (!project) return;
    setUploading(true); setNotice(''); setInjection(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const result = await api.upload<any>(`/api/projects/${project.id}/requirements/upload`, form);
      const s = result.summary ?? {};
      setNotice(
        `Extracted ${s.count ?? 0} requirement(s): ${s.open_questions ?? 0} open question(s), ` +
        `${s.inferred_refs ?? 0} inferred reference(s).` +
        (result.warnings?.length ? ` ${result.warnings.join(' ')}` : ''),
      );
      if (result.injection_scan?.suspicious) setInjection(result.injection_scan);
      await load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'upload failed');
    } finally { setUploading(false); }
  };

  const generate = async () => {
    if (!project) return;
    setGenerating(true);
    try {
      const result = await api.post<any>(`/api/projects/${project.id}/requirements/generate`, { persist: true });
      setNotice(result.note);
      await load();
      void refreshOverview();
    } finally { setGenerating(false); }
  };

  const downloadTemplate = () => {
    if (!project) return;
    void downloadBlob(`/api/projects/${project.id}/requirements/template`, 'sample-requirements.md');
  };

  const runSampleFloor = async () => {
    if (!project) return;
    setSampling(true); setNotice(''); setFloorResult(null);
    try {
      const result = await api.post<any>(`/api/projects/${project.id}/requirements/sample`, { run: true });
      if (result.ok === false) {
        setNotice(result.error || 'the sample run could not complete');
      } else {
        setFloorResult(result);
        setNotice(
          `Sample floor complete: ${result.requirements?.cases_generated ?? 0} requirement-derived ` +
          `case(s), run #${result.run_number} (${result.status}). Reports are ready below.`,
        );
      }
      await load();
      void refreshOverview();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'the sample run failed');
    } finally { setSampling(false); }
  };

  return (
    <div className="space-y-3 p-3">
      {/* --- ingest ------------------------------------------------------ */}
      <Panel className="p-3">
        <div className="flex flex-wrap items-center gap-3">
          <div
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const file = e.dataTransfer.files[0];
              if (file) void upload(file);
            }}
            onClick={() => fileInput.current?.click()}
            className="rounded-lg flex min-w-0 flex-1 cursor-pointer items-center gap-3 border border-dashed border-line-strong px-3 py-3 transition hover:border-accent/40 hover:bg-surface-2"
          >
            {uploading ? <Spinner className="text-accent" /> : <FileUp size={18} className="shrink-0 text-ink-3" />}
            <div className="min-w-0">
              <p className="text-[12.5px] font-medium text-ink">
                {uploading ? 'Reading the document…' : 'Drop a requirement document, or click to choose'}
              </p>
              <p className="text-[11px] text-ink-3">
                PDF, DOCX, Markdown or plain text. Your customer's own requirement
                identifiers are preserved end to end.
              </p>
            </div>
          </div>
          <input
            ref={fileInput} type="file" hidden
            accept=".pdf,.docx,.md,.txt,.markdown"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); }}
          />
          <Button variant="primary" onClick={generate} disabled={generating || items.length === 0}>
            {generating ? <Spinner /> : <Sparkles size={13} />}
            Generate test proposals
          </Button>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-line pt-2">
          <p className="text-[11px] text-ink-3">
            New here? Try the bundled sample spec (no writing required):
          </p>
          <Button onClick={downloadTemplate}>
            <Download size={12} /> Download the template
          </Button>
          <Button variant="primary" onClick={runSampleFloor} disabled={sampling}>
            {sampling ? <Spinner /> : <PlayCircle size={13} />}
            {sampling ? 'Running the full floor…' : 'Run the full floor with the sample'}
          </Button>
        </div>

        {notice && <p className="mt-2 text-[11.5px] text-ink-2">{notice}</p>}

        {floorResult?.reports && (
          <div className="rounded-lg mt-2 flex flex-wrap items-center gap-3 border border-line bg-surface-2 p-2.5 text-[11.5px]">
            <span className="font-medium text-ink">Sample floor reports:</span>
            <a className="text-accent hover:underline" href={floorResult.reports.test_plan_docx} target="_blank" rel="noreferrer">Test Plan (Word)</a>
            <a className="text-accent hover:underline" href={floorResult.reports.docx} target="_blank" rel="noreferrer">Completion Report (Word)</a>
            <a className="text-accent hover:underline" href={floorResult.reports.xlsx} target="_blank" rel="noreferrer">Completion Report (Excel)</a>
            <a className="text-accent hover:underline" href={floorResult.reports.xlsx_rtm} target="_blank" rel="noreferrer">Traceability (Excel)</a>
          </div>
        )}

        {injection && (
          <div className="rounded-lg mt-2 border border-fail/30 bg-fail/[0.07] p-2.5">
            <div className="flex items-center gap-2 text-[12px] font-medium text-fail">
              <AlertTriangle size={13} /> This document contains text that looks like instructions to the agent
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-ink-2">
              It was treated strictly as data and never as a command. Nothing was silently
              removed. Review these passages before trusting the document.
            </p>
            <ul className="mt-1.5 space-y-1">
              {(injection.findings ?? []).map((f: any, i: number) => (
                <li key={i} className="mono truncate text-[10.5px] text-ink-3">
                  [{f.kind}/{f.severity}] {f.excerpt}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Panel>

      {coverage && (
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
          {/* --- coverage --------------------------------------------- */}
          <Panel className="overflow-hidden">
            <SectionTitle hint="gaps first">Coverage</SectionTitle>
            <div className="space-y-3 px-4 pb-3">
              <p className="text-[12.5px] leading-relaxed text-ink-2">{coverage.headline}</p>

              <div className="grid grid-cols-2 gap-2">
                <MetricCard
                  label="Covered" pct={coverage.coverage_pct}
                  legend={<>
                    <BandRow tone="pass" label="Strong" range="≥ 80%" />
                    <BandRow tone="flaky" label="Improving" range="40–79%" />
                    <BandRow tone="fail" label="Needs work" range="< 40%" />
                  </>}
                />
                <MetricCard
                  label="Automated" pct={coverage.automation_pct}
                  legend={<>
                    <BandRow tone="pass" label="Strong" range="≥ 80%" />
                    <BandRow tone="flaky" label="Improving" range="40–79%" />
                    <BandRow tone="fail" label="Needs work" range="< 40%" />
                    <p className="pt-1 text-ink-3">Automated = has a runnable, approved test.</p>
                  </>}
                />
              </div>

              <div className="grid grid-cols-4 gap-1.5">
                {Object.entries(coverage.by_risk ?? {}).map(([risk, data]: any) => (
                  <div
                    key={risk}
                    className={clsx('rounded-lg border p-1.5 text-center', RISK_TIER_STYLE[risk] ?? RISK_TIER_STYLE.medium)}
                  >
                    <p className="truncate text-[10px] capitalize text-ink-3">{risk}</p>
                    <p className="mono text-[12px] font-semibold text-ink">{data.covered}/{data.total}</p>
                  </div>
                ))}
              </div>

              {coverage.uncovered.length > 0 && (
                <div>
                  <p className="mb-1 text-[11px] font-medium text-flaky">
                    Untested ({coverage.uncovered.length})
                  </p>
                  <div className="max-h-40 space-y-1 overflow-y-auto">
                    {coverage.uncovered.map((gap: any) => (
                      <div key={gap.ref} className="flex items-baseline gap-2 text-[11.5px]">
                        <span className={clsx('shrink-0 border px-1 text-[9.5px]', RISK_COLOR[gap.risk] ?? RISK_COLOR.medium)}>
                          {gap.ref}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-ink-3" title={gap.gap_reason}>{gap.title}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {coverage.weak.length > 0 && (
                <div>
                  <p className="mb-1 text-[11px] font-medium text-ink-2">
                    Covered, but weakly ({coverage.weak.length})
                  </p>
                  <div className="max-h-32 space-y-1 overflow-y-auto">
                    {coverage.weak.map((w: any) => (
                      <div key={w.ref} className="text-[11px]">
                        <span className="mono text-ink-3">{w.ref}</span>
                        <span className="ml-1.5 text-ink-3">{w.weakness?.[0]}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </Panel>

          {/* --- traceability matrix ------------------------------------ */}
          <Panel className="overflow-hidden">
            <SectionTitle hint={`${matrix.length} requirement(s)`}>Traceability</SectionTitle>
            <div className="max-h-[480px] overflow-y-auto border-t border-line">
              {matrix.length === 0 && <Empty title="No requirements ingested" />}
              {matrix.map((row) => (
                <div key={row.ref} className="border-b border-line/60 px-4 py-2 last:border-0">
                  <div className="flex items-baseline gap-2">
                    <span className={clsx('shrink-0 border px-1 text-[10px]', RISK_COLOR[row.risk] ?? RISK_COLOR.medium)}>
                      {row.ref}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink-2">{row.title}</span>
                    {row.covered
                      ? <Chip tone="good">covered</Chip>
                      : <Chip tone="danger">no test</Chip>}
                  </div>

                  {row.open_questions?.length > 0 && (
                    <div className="mt-1 space-y-0.5 pl-1">
                      {row.open_questions.map((q: string, i: number) => (
                        <p key={i} className="flex items-baseline gap-1.5 text-[10.5px] text-flaky">
                          <HelpCircle size={9} className="shrink-0" /> {q}
                        </p>
                      ))}
                    </div>
                  )}

                  {row.tests.length > 0 && (
                    <div className="mt-1 space-y-0.5 pl-1">
                      {row.tests.map((t: any) => (
                        <div key={t.key} className="flex items-baseline gap-2 text-[11px]">
                          <span className="mono shrink-0 text-ink-3">{t.key}</span>
                          <span className="min-w-0 flex-1 truncate text-ink-3">{t.title}</span>
                          <Chip>{t.category}</Chip>
                          <span className={clsx(
                            'shrink-0 text-[10px]',
                            t.last_status === 'passed' ? 'text-pass'
                              : t.last_status === 'failed' ? 'text-fail' : 'text-ink-3',
                          )}>
                            {t.last_status}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}

/** A headline percentage as its own card: big number, a status word instead of
 *  a bare figure, and an (i) explaining the bands behind that word. The same
 *  shape as a metric card in a call-analytics dashboard (latency, CSAT, ...),
 *  applied to a coverage number instead. */
function MetricCard({ label, pct, legend }: {
  label: string; pct: number; legend: ReactNode;
}) {
  const status = bandFor(pct);
  return (
    <div className="rounded-lg border border-line bg-surface-2 p-2.5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-ink-3">{label}</span>
        <InfoPopover label={`What counts as ${label.toLowerCase()}?`}>{legend}</InfoPopover>
      </div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="text-2xl font-bold tabular-nums text-ink">{pct}%</span>
        <Chip tone={CHIP_TONE[status.tone]}>{status.label}</Chip>
      </div>
      <Meter value={pct} tone={status.tone === 'pass' ? 'pass' : status.tone === 'fail' ? 'fail' : 'flaky'} className="mt-2" />
    </div>
  );
}
