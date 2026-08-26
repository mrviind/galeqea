import { useCallback, useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import { AlertTriangle, FileUp, HelpCircle, Sparkles, Upload } from 'lucide-react';
import { api } from '../lib/api';
import type { Coverage } from '../lib/api';
import { RISK_COLOR } from '../lib/format';
import { useApp } from '../state';
import { Button, Chip, Empty, Meter, Panel, SectionTitle, Spinner } from '../components/primitives';

export default function Requirements() {
  const { project, refreshOverview } = useApp();
  const [items, setItems] = useState<any[]>([]);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [matrix, setMatrix] = useState<any[]>([]);
  const [uploading, setUploading] = useState(false);
  const [generating, setGenerating] = useState(false);
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
        `Extracted ${s.count ?? 0} requirement(s) — ${s.open_questions ?? 0} open question(s), ` +
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

        {notice && <p className="mt-2 text-[11.5px] text-ink-2">{notice}</p>}

        {injection && (
          <div className="rounded-lg mt-2 border border-fail/30 bg-fail/[0.07] p-2.5">
            <div className="flex items-center gap-2 text-[12px] font-medium text-fail">
              <AlertTriangle size={13} /> This document contains text that looks like instructions to the agent
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-ink-2">
              It was treated strictly as data and never as a command. Nothing was silently
              removed — review these passages before trusting the document.
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
              <div>
                <div className="flex justify-between text-[11px] text-ink-3">
                  <span>Covered</span><span className="mono">{coverage.coverage_pct}%</span>
                </div>
                <Meter value={coverage.coverage_pct} tone="pass" className="mt-1" />
              </div>
              <div>
                <div className="flex justify-between text-[11px] text-ink-3">
                  <span>Automated</span><span className="mono">{coverage.automation_pct}%</span>
                </div>
                <Meter value={coverage.automation_pct} className="mt-1" />
              </div>

              <div className="grid grid-cols-4 gap-1.5">
                {Object.entries(coverage.by_risk ?? {}).map(([risk, data]: any) => (
                  <div key={risk} className="rounded-lg border border-line bg-surface-2 p-1.5 text-center">
                    <p className="text-[10px] capitalize text-ink-3">{risk}</p>
                    <p className="mono text-[12px] text-ink">{data.covered}/{data.total}</p>
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
