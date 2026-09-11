import { useRef, useState } from 'react';
import { Upload } from 'lucide-react';
import { api } from '../lib/api';
import { useApp } from '../state';
import { Button, Panel, SectionTitle, Spinner } from './primitives';

/** Settings/Author → Import: bring test cases (Gherkin/CSV → proposals) or results
 *  (JUnit → a run) in from another tool. A CSV proposes a column mapping to confirm. */
export function ImportPanel() {
  const { project } = useApp();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [pending, setPending] = useState<{ file: File; mapping: Record<string, string> } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const submit = async (file: File, mapping?: Record<string, string>) => {
    if (!project) return;
    setBusy(true); setMsg('');
    const form = new FormData();
    form.append('file', file);
    if (mapping) form.append('mapping', JSON.stringify(mapping));
    try {
      const out = await api.upload<any>(`/api/projects/${project.id}/tests/import`, form);
      if (out.needs_mapping) {
        setPending({ file, mapping: out.suggested });
        setMsg(`CSV columns: ${out.headers.join(', ')}. Confirm the mapping below.`);
      } else if (out.run_id) {
        setPending(null);
        setMsg(`Imported ${out.count} result(s) → run #${out.run_number} (${out.passed} passed, ${out.failed} failed).`);
      } else {
        setPending(null);
        setMsg(`Imported ${out.count} test case(s) as proposals. Review them in the board.`);
      }
    } catch (e: any) {
      setMsg(e?.message || 'import failed');
    } finally { setBusy(false); }
  };

  return (
    <Panel>
      <SectionTitle hint="Gherkin / CSV → proposals · JUnit → a run">Import from another tool</SectionTitle>
      <div className="space-y-2 px-4 py-3">
        <input
          ref={inputRef} type="file" accept=".feature,.csv,.xml,.json" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void submit(f); e.target.value = ''; }} />
        <Button variant="ghost" disabled={busy} onClick={() => inputRef.current?.click()}>
          {busy ? <Spinner /> : <Upload size={13} />} Choose a file
        </Button>

        {pending && (
          <div className="rounded-md border border-line bg-surface-2/40 p-2.5">
            <p className="mb-1.5 text-[11px] text-ink-3">Column mapping (edit if needed):</p>
            <div className="grid grid-cols-2 gap-1.5">
              {['title', 'steps', 'expected', 'priority', 'tags'].map((field) => (
                <label key={field} className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-ink-3">{field}</span>
                  <input
                    value={pending.mapping[field] ?? ''}
                    onChange={(e) => setPending({ ...pending, mapping: { ...pending.mapping, [field]: e.target.value } })}
                    className="rounded border border-line bg-surface px-1.5 py-1 text-[11px] text-ink outline-none focus:border-accent" />
                </label>
              ))}
            </div>
            <Button size="sm" variant="primary" disabled={busy} className="mt-2"
              onClick={() => submit(pending.file, pending.mapping)}>
              Import with this mapping
            </Button>
          </div>
        )}
        {msg && <p className="text-[11px] text-ink-2">{msg}</p>}
      </div>
    </Panel>
  );
}
