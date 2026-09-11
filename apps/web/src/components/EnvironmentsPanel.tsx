import { useEffect, useState } from 'react';
import { Plus, Server } from 'lucide-react';
import { releases, type Environment } from '../lib/api';
import { useApp } from '../state';
import { Button, Chip, Empty, Panel, SectionTitle, Spinner } from './primitives';

/** Settings → Environments: the places a plan's cycles run against. */
export function EnvironmentsPanel() {
  const { project } = useApp();
  const [list, setList] = useState<Environment[] | null>(null);
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const load = () => {
    if (!project) return;
    releases.environments(project.id).then((r) => setList(r.environments), () => setList([]));
  };
  useEffect(load, [project]);

  async function add() {
    if (!project || !name.trim() || !url.trim()) return;
    setBusy(true); setErr('');
    try {
      await releases.addEnvironment(project.id, { name: name.trim(), base_url: url.trim() });
      setName(''); setUrl(''); load();
    } catch (e: any) { setErr(e?.message || 'could not add the environment'); }
    finally { setBusy(false); }
  }

  const field = 'h-9 rounded-lg border border-line bg-surface-2 px-3 text-[13px] text-ink placeholder:text-ink-3 outline-none transition focus:border-accent';

  return (
    <Panel className="overflow-hidden">
      <SectionTitle hint="a plan runs its cycles against these">Environments</SectionTitle>
      <div className="flex flex-col gap-2 px-4 py-3">
        <div className="flex gap-2">
          <input className={`${field} w-40`} placeholder="name (e.g. staging)" value={name} onChange={(e) => setName(e.target.value)} />
          <input className={`${field} flex-1`} placeholder="https://staging.example.com" value={url} onChange={(e) => setUrl(e.target.value)} />
          <Button variant="primary" onClick={add} disabled={busy || !name.trim() || !url.trim()}>
            {busy ? <Spinner className="text-canvas" /> : <Plus size={14} />} Add
          </Button>
        </div>
        {err && <p className="text-[11px] text-fail">{err}</p>}
      </div>
      <div className="border-t border-line">
        {list === null && <div className="p-4"><Spinner className="text-ink-3" /></div>}
        {list?.length === 0 && <Empty icon={<Server size={16} />} title="No environments yet" body="Add one, or say “add environment staging https://…” in chat." />}
        {list?.map((e) => (
          <div key={e.id} className="flex items-center gap-2 border-b border-line/60 px-4 py-2 last:border-0">
            <Server size={13} className="shrink-0 text-ink-3" />
            <span className="text-[12.5px] text-ink">{e.name}</span>
            <a href={e.base_url} target="_blank" rel="noreferrer" className="mono min-w-0 flex-1 truncate text-[11px] text-accent hover:underline">{e.base_url}</a>
            {(e.tags ?? []).map((t) => <Chip key={t}>{t}</Chip>)}
          </div>
        ))}
      </div>
    </Panel>
  );
}
