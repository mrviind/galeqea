import { useEffect, useState } from 'react';
import { Copy, KeyRound, Plus, Trash2 } from 'lucide-react';
import { ApiToken, tokens } from '../lib/api';
import { Button, Chip, Empty, Panel, SectionTitle, Spinner } from './primitives';

const SCOPES = ['runs:write', 'projects:read', 'reports:read', 'approvals:decide'];

/** Create, list and revoke scoped API tokens for CI, the MCP server and the CLI. */
export function ApiTokensPanel() {
  const [list, setList] = useState<ApiToken[] | null>(null);
  const [name, setName] = useState('');
  const [scopes, setScopes] = useState<string[]>(['projects:read']);
  const [created, setCreated] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = () => tokens.list().then(setList, () => setList([]));
  useEffect(() => { load(); }, []);

  function toggleScope(s: string) {
    setScopes((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));
  }

  async function create() {
    if (!name.trim() || scopes.length === 0) return;
    setBusy(true); setError('');
    try {
      const t = await tokens.create(name.trim(), scopes);
      setCreated(t.token);
      setName('');
      await load();
    } catch (e: any) {
      setError(e?.message || 'Could not create the token');
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    await tokens.revoke(id);
    await load();
  }

  const field =
    'h-9 w-full rounded-lg border border-line bg-surface-2 px-3 text-[13px] text-ink ' +
    'placeholder:text-ink-3 outline-none transition focus:border-accent';

  return (
    <Panel className="overflow-hidden">
      <SectionTitle hint="least-privilege; hashed at rest; shown once">API tokens</SectionTitle>

      <div className="space-y-3 px-4 py-3">
        {created && (
          <div className="rounded-lg border border-pass/40 bg-pass/10 p-3">
            <p className="mb-1 text-[11px] font-medium text-pass">
              Copy this token now. It is never shown again.
            </p>
            <div className="flex items-center gap-2">
              <code className="mono min-w-0 flex-1 truncate rounded bg-surface-3 px-2 py-1 text-[11px] text-ink">
                {created}
              </code>
              <button
                onClick={() => navigator.clipboard?.writeText(created)}
                className="shrink-0 text-ink-3 transition hover:text-ink" title="Copy"
              >
                <Copy size={14} />
              </button>
              <button
                onClick={() => setCreated(null)}
                className="shrink-0 text-[11px] text-ink-3 transition hover:text-ink"
              >
                Done
              </button>
            </div>
          </div>
        )}

        <div className="flex flex-col gap-2">
          <input
            className={field} value={name} placeholder="Token name (e.g. CI pipeline)"
            onChange={(e) => setName(e.target.value)}
          />
          <div className="flex flex-wrap gap-1.5">
            {SCOPES.map((s) => (
              <button
                key={s} onClick={() => toggleScope(s)}
                className={
                  'rounded-md border px-2 py-0.5 text-[11px] transition ' +
                  (scopes.includes(s)
                    ? 'border-accent/50 bg-accent/15 text-accent'
                    : 'border-line bg-surface-2 text-ink-3 hover:text-ink')
                }
              >
                {s}
              </button>
            ))}
          </div>
          {error && <p className="text-[11px] text-fail">{error}</p>}
          <Button
            variant="primary" onClick={create}
            disabled={busy || !name.trim() || scopes.length === 0} className="w-fit"
          >
            {busy ? <Spinner className="text-canvas" /> : <Plus size={14} />} Create token
          </Button>
        </div>
      </div>

      <div className="border-t border-line">
        {list === null && <div className="p-4"><Spinner className="text-ink-3" /></div>}
        {list?.length === 0 && (
          <Empty icon={<KeyRound size={16} />} title="No tokens yet"
                 body="Create one for CI, the MCP server or the CLI." />
        )}
        {list?.map((t) => (
          <div key={t.id} className="flex items-center gap-2 border-b border-line/60 px-4 py-2 last:border-0">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-[12.5px] text-ink">{t.name}</span>
                <code className="mono text-[10px] text-ink-3">{t.prefix}…</code>
                {t.revoked && <Chip tone="danger">revoked</Chip>}
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
                {t.scopes.map((s) => <Chip key={s} tone="neutral">{s}</Chip>)}
                <span className="text-[10px] text-ink-3">
                  {t.last_used_at ? `used ${new Date(t.last_used_at).toLocaleDateString()}` : 'never used'}
                  {t.expires_at ? ` · expires ${new Date(t.expires_at).toLocaleDateString()}` : ''}
                </span>
              </div>
            </div>
            {!t.revoked && (
              <button
                onClick={() => revoke(t.id)}
                className="shrink-0 text-ink-3 transition hover:text-fail" title="Revoke"
              >
                <Trash2 size={14} />
              </button>
            )}
          </div>
        ))}
      </div>
    </Panel>
  );
}
