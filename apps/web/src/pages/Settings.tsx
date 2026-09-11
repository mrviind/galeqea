import { useCallback, useEffect, useState } from 'react';
import clsx from 'clsx';
import {
  CheckCircle2, Coins, Cpu, KeyRound, Lock, Plug, Plus, ShieldCheck,
  Trash2, WifiOff, XCircle,
} from 'lucide-react';
import { api } from '../lib/api';
import { useApp } from '../state';
import { Button, Chip, Empty, Meter, Panel, SectionTitle, Spinner } from '../components/primitives';
import { ApiTokensPanel } from '../components/ApiTokensPanel';
import { EnvironmentsPanel } from '../components/EnvironmentsPanel';
import { IntegrationsPanel } from '../components/IntegrationsPanel';

export default function Settings() {
  const { project, capabilities } = useApp();
  const [settings, setSettings] = useState<any>(null);
  const [mode, setMode] = useState('no_ai');
  const [provider, setProvider] = useState('none');
  const [model, setModel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [health, setHealth] = useState<any>(null);
  const [audit, setAudit] = useState<any>(null);
  const [keys, setKeys] = useState<any[]>([]);
  const [usage, setUsage] = useState<any>(null);

  const load = useCallback(async () => {
    const [s, k, u] = await Promise.all([
      api.get<any>('/api/settings'),
      api.get<any>('/api/settings/keys'),
      api.get<any>('/api/settings/usage?days=30'),
    ]);
    setSettings(s); setKeys(k.keys); setUsage(u);
    setMode(s.ai.mode); setProvider(s.ai.provider);
    setModel(s.ai.model); setBaseUrl(s.ai.base_url);
    if (project) {
      // Integrations are owned by IntegrationsPanel now; here we only need the audit.
      setAudit(await api.get<any>(`/api/projects/${project.id}/audit?limit=25`));
    }
  }, [project]);

  useEffect(() => { void load(); }, [load]);

  const save = async () => {
    setSaving(true);
    try {
      const result = await api.post<any>('/api/settings/model', {
        mode, provider, model, base_url: baseUrl,
        ...(apiKey ? { api_key: apiKey } : {}),
      });
      setHealth(result.health);
      setApiKey('');
      await load();
    } finally { setSaving(false); }
  };

  if (!settings) return <div className="flex h-40 items-center justify-center"><Spinner /></div>;

  const selectedMode = capabilities?.ai_modes?.find((m: any) => m.mode === mode);

  return (
    <div className="grid gap-3 p-3 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
      <div className="space-y-3">
        {/* --- model ---------------------------------------------------- */}
        <Panel className="overflow-hidden">
          <SectionTitle hint="AI is optional: everything core works without it">Model</SectionTitle>
          <div className="space-y-3 px-4 pb-4">
            <div className="grid gap-1.5">
              {(capabilities?.ai_modes ?? []).map((m: any) => (
                <button
                  key={m.mode}
                  onClick={() => {
                    setMode(m.mode);
                    if (m.mode === 'no_ai') setProvider('none');
                    else if (m.providers?.length) setProvider(m.providers[0]);
                  }}
                  className={clsx(
                    'border p-2.5 text-left transition',
                    mode === m.mode
                      ? 'border-accent/40 bg-accent/[0.07]'
                      : 'border-line bg-surface-2 hover:border-line-strong',
                  )}
                >
                  <div className="flex items-center gap-2">
                    {m.mode === 'no_ai' ? <WifiOff size={13} className="text-ink-3" /> : <Cpu size={13} className="text-accent" />}
                    <span className="text-[12.5px] font-medium text-ink">{m.label}</span>
                    {m.default && <Chip>default</Chip>}
                  </div>
                  <p className="mt-1 text-[11px] leading-relaxed text-ink-3">{m.description}</p>
                </button>
              ))}
            </div>

            {mode !== 'no_ai' && <RoleModels />}

            {mode !== 'no_ai' && (
              <div className="space-y-2 border-t border-line pt-3">
                <Field label="Provider">
                  <select aria-label="Model provider"
                    value={provider}
                    onChange={(e) => setProvider(e.target.value)}
                    className="rounded-lg w-full border border-line bg-surface-2 px-2.5 py-1.5 text-[12px] outline-none focus:border-accent/40"
                  >
                    {(selectedMode?.providers ?? [provider]).map((p: string) => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Model">
                  <input
                    value={model} onChange={(e) => setModel(e.target.value)}
                    placeholder="claude-opus-5"
                    className="rounded-lg w-full border border-line bg-surface-2 px-2.5 py-1.5 text-[12px] outline-none placeholder:text-ink-3 focus:border-accent/40"
                  />
                </Field>
                {(mode === 'local' || provider === 'openai_compatible') && (
                  <Field label="Endpoint">
                    <input
                      value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
                      placeholder="http://localhost:11434/v1"
                      className="rounded-lg w-full border border-line bg-surface-2 px-2.5 py-1.5 text-[12px] outline-none placeholder:text-ink-3 focus:border-accent/40"
                    />
                  </Field>
                )}
                {mode === 'api_key' && (
                  <Field label="API key" hint={settings.ai.api_key_set ? 'a key is already stored' : 'sealed in the local vault'}>
                    <input
                      type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
                      placeholder={settings.ai.api_key_set ? '•••••••• (leave blank to keep)' : 'sk-…'}
                      className="rounded-lg w-full border border-line bg-surface-2 px-2.5 py-1.5 text-[12px] outline-none placeholder:text-ink-3 focus:border-accent/40"
                    />
                  </Field>
                )}
              </div>
            )}

            <div className="flex items-center gap-2">
              <Button variant="primary" onClick={save} disabled={saving}>
                {saving ? <Spinner /> : <CheckCircle2 size={13} />} Save & test
              </Button>
              {health && (
                <span className={clsx(
                  'flex items-center gap-1.5 text-[11.5px]',
                  health.status === 'ready' ? 'text-pass' : health.status === 'no_ai_mode' ? 'text-ink-3' : 'text-fail',
                )}>
                  {health.status === 'ready' ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
                  {health.status}{health.detail ? `: ${health.detail}` : ''}
                </span>
              )}
            </div>
          </div>
        </Panel>

        <ApiKeys keys={keys} usage={usage} onChange={load} />

        {/* --- integrations ---------------------------------------------- */}
        <IntegrationsPanel />

        {/* --- webhooks -------------------------------------------------- */}
        <WebhooksPanel />
      </div>

      <div className="space-y-3">
        {/* --- governance ------------------------------------------------ */}
        <Panel className="overflow-hidden">
          <SectionTitle>Governance</SectionTitle>
          <div className="space-y-2 px-4 pb-4">
            <div className="rounded-lg flex items-center gap-2 border border-pass/25 bg-pass/[0.06] p-2.5">
              <ShieldCheck size={15} className="shrink-0 text-pass" />
              <div>
                <p className="text-[12px] font-medium text-ink">AI self-approval is structurally prohibited</p>
                <p className="text-[10.5px] leading-relaxed text-ink-3">
                  Enforced in code, not by configuration. A machine principal can never satisfy
                  an approval gate, and a requester can never approve their own request.
                </p>
              </div>
            </div>
            <Row label="Approval mode" value={settings.governance.approval_mode} />
            <Row label="Gated actions" value={`${capabilities?.approval_actions.length ?? 0}`} />
            <Row label="Telemetry" value={settings.telemetry_enabled ? 'on' : 'off, and off by default'} />
            <Row label="Web research" value={settings.ai.web_research_enabled ? 'enabled' : 'disabled (opt-in)'} />
            <div className="flex items-center justify-between py-1 text-[12px]">
              <span className="text-ink-3">Enterprise readiness</span>
              <a
                href="https://github.com/mrviind/galeqea/blob/main/docs/ENTERPRISE.md"
                target="_blank" rel="noreferrer"
                className="text-accent hover:underline">SSO · audit · SBOM checklist →</a>
            </div>
            {capabilities?.build && (
              <>
                <Row
                  label="Build"
                  value={`v${capabilities.build.version} · ${capabilities.build.sha}`}
                />
                <Row
                  label="UI built"
                  value={capabilities.build.built_at
                    ? new Date(capabilities.build.built_at).toLocaleString()
                    : '-'}
                />
              </>
            )}

            {audit?.chain && (
              <div className={clsx(
                'mt-2 flex items-start gap-2 border p-2.5',
                audit.chain.ok ? 'border-pass/25 bg-pass/[0.06]' : 'border-fail/30 bg-fail/[0.07]',
              )}>
                <KeyRound size={14} className={clsx('mt-px shrink-0', audit.chain.ok ? 'text-pass' : 'text-fail')} />
                <div>
                  <p className={clsx('text-[12px] font-medium', audit.chain.ok ? 'text-pass' : 'text-fail')}>
                    Audit ledger {audit.chain.ok ? 'verified' : 'BROKEN'}
                  </p>
                  <p className="text-[10.5px] leading-relaxed text-ink-3">
                    {audit.chain.ok
                      ? `${audit.chain.checked} hash-chained entries verified end to end.`
                      : audit.chain.reason}
                  </p>
                </div>
                <a
                  href={`/api/projects/${project?.id}/audit/export`}
                  className="ml-auto shrink-0 text-[10.5px] text-ink-3 underline transition hover:text-ink-2"
                >
                  Export
                </a>
              </div>
            )}
          </div>
        </Panel>

        {/* --- environments ----------------------------------------------- */}
        <EnvironmentsPanel />

        {/* --- API tokens ------------------------------------------------- */}
        <ApiTokensPanel />

        {/* --- audit trail ------------------------------------------------ */}
        <Panel className="overflow-hidden">
          <SectionTitle hint="append-only, hash-chained">Recent activity</SectionTitle>
          <div tabIndex={0} className="max-h-[360px] overflow-y-auto border-t border-line">
            {(audit?.events ?? []).length === 0 && <Empty title="No activity yet" />}
            {(audit?.events ?? []).map((event: any) => (
              <div key={event.seq} className="flex items-baseline gap-2 border-b border-line/60 px-4 py-1.5 last:border-0">
                <span className="mono w-8 shrink-0 text-right text-[10px] text-ink-3">{event.seq}</span>
                <span className="mono shrink-0 text-[10.5px] text-accent">{event.action}</span>
                <span className="min-w-0 flex-1 truncate text-[11px] text-ink-3">
                  {event.actor} · {event.resource_type}
                </span>
                <span className="mono shrink-0 text-[9.5px] text-ink-3">{event.entry_hash}</span>
              </div>
            ))}
          </div>
        </Panel>

        {/* --- capabilities ----------------------------------------------- */}
        <Panel className="overflow-hidden">
          <SectionTitle hint={`${capabilities?.tools.length ?? 0} tools · also exposed over MCP`}>
            Agent capabilities
          </SectionTitle>
          <div tabIndex={0} className="max-h-[300px] overflow-y-auto border-t border-line">
            {(capabilities?.tools ?? []).map((tool) => (
              <div key={tool.name} className="flex items-baseline gap-2 border-b border-line/60 px-4 py-1.5 last:border-0">
                <span className="mono shrink-0 text-[10.5px] text-ink-2">{tool.name}</span>
                {tool.read_only
                  ? <Chip tone="good">read</Chip>
                  : <Chip tone={tool.external ? 'danger' : 'warn'}>{tool.external ? 'external' : 'write'}</Chip>}
                {tool.requires_approval && <Chip tone="brand"><Lock size={9} /> gated</Chip>}
                <span className="min-w-0 flex-1 truncate text-right text-[10.5px] text-ink-3">{tool.category}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 flex items-baseline gap-2">
        <span className="text-[11px] font-medium text-ink-2">{label}</span>
        {hint && <span className="text-[10px] text-ink-3">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between border-b border-line/60 py-1.5 last:border-0">
      <span className="text-[11.5px] text-ink-3">{label}</span>
      <span className="mono text-[11px] text-ink-2">{value}</span>
    </div>
  );
}


// --------------------------------------------------------------------------- //
/**
 * Per-role model routing (WO#8-C).
 *
 * The whole point of GaleQEA is that a green re-run costs nothing. When the model
 * *is* in the loop (building a step, healing a broken locator, judging a verdict),
 * the roles have wildly different needs: locating wants a cheap, fast model; judging
 * wants a careful one. This routes each role to its own model so you pay frontier
 * prices only where they earn their keep. Blank = inherit the provider default.
 */
const ROLE_ROWS: { key: string; label: string; hint: string }[] = [
  { key: 'planner', label: 'Planner', hint: 'authoring & exploration (reasoning)' },
  { key: 'grounder', label: 'Grounder', hint: 'locating & self-healing (cheap/fast)' },
  { key: 'judge', label: 'Judge', hint: 'verdicts & RCA (careful)' },
];

function RoleModels() {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');

  const load = useCallback(async () => {
    try {
      const r = await api.get<any>('/api/settings/role-models');
      setModels(r.role_models || {});
      setSaved(r.role_models || {});
    } catch { /* provider not configured yet, leave blank */ }
  }, []);
  useEffect(() => { if (open) void load(); }, [open, load]);

  const dirty = JSON.stringify(models) !== JSON.stringify(saved);

  async function save() {
    setSaving(true); setMsg('');
    try {
      const r = await api.post<any>('/api/settings/role-models', { role_models: models });
      setSaved(r.role_models || {});
      setModels(r.role_models || {});
      setMsg('routing saved');
    } catch (e: any) {
      setMsg(e?.message || 'could not save');
    } finally { setSaving(false); }
  }

  return (
    <div className="border-t border-line pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between text-left"
      >
        <span className="text-[11px] font-medium text-ink-2">Per-role model routing</span>
        <span className="text-[10px] text-ink-3">{open ? 'hide' : 'a cheaper model for locating →'}</span>
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {ROLE_ROWS.map((r) => (
            <div key={r.key} className="grid grid-cols-[92px_1fr] items-center gap-2">
              <div className="leading-tight">
                <div className="text-[11.5px] text-ink-2">{r.label}</div>
                <div className="text-[9.5px] text-ink-3">{r.hint}</div>
              </div>
              <input
                value={models[r.key] || ''}
                onChange={(e) => setModels((m) => ({ ...m, [r.key]: e.target.value }))}
                placeholder="inherit default"
                className="rounded-lg w-full border border-line bg-surface-2 px-2.5 py-1.5 text-[12px] outline-none placeholder:text-ink-3 focus:border-accent/40"
              />
            </div>
          ))}
          <div className="flex items-center gap-2 pt-0.5">
            <Button variant="ghost" onClick={save} disabled={saving || !dirty}>
              {saving ? <Spinner /> : null} Save routing
            </Button>
            {msg && <span className="text-[11px] text-ink-3">{msg}</span>}
          </div>
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
/**
 * Bring your own key.
 *
 * Three properties make this real rather than a settings field: the key is
 * probed against the provider *before* it is stored, it is sealed in the local
 * vault so it survives a restart, and a project key overrides the global one so
 * one workspace can use a local model while another uses a hosted one.
 */
function ApiKeys({ keys, usage, onChange }: { keys: any[]; usage: any; onChange: () => void }) {
  const { project } = useApp();
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({
    provider: 'anthropic', api_key: '', model: '', base_url: '',
    monthly_budget_usd: '', scoped: false,
  });
  const [probe, setProbe] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const PROVIDERS = ['anthropic', 'openai', 'gemini', 'azure_openai', 'ollama', 'openai_compatible'];
  const needsEndpoint = ['ollama', 'openai_compatible', 'azure_openai'].includes(draft.provider);

  const test = async () => {
    setBusy(true); setError(''); setProbe(null);
    try {
      setProbe(await api.post('/api/settings/keys/verify', {
        provider: draft.provider, api_key: draft.api_key,
        model: draft.model, base_url: draft.base_url,
      }));
    } finally { setBusy(false); }
  };

  const save = async () => {
    setBusy(true); setError('');
    try {
      await api.post('/api/settings/keys', {
        provider: draft.provider,
        api_key: draft.api_key,
        model: draft.model,
        base_url: draft.base_url,
        monthly_budget_usd: Number(draft.monthly_budget_usd) || 0,
        ...(draft.scoped && project ? { project_id: project.id } : {}),
      });
      setDraft({ provider: 'anthropic', api_key: '', model: '', base_url: '', monthly_budget_usd: '', scoped: false });
      setProbe(null); setAdding(false); onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'could not save that key');
    } finally { setBusy(false); }
  };

  const revoke = async (provider: string, scope: string) => {
    const query = scope === 'global' ? '' : `?project_id=${scope}`;
    await api.del(`/api/settings/keys/${provider}${query}`);
    onChange();
  };

  return (
    <Panel className="overflow-hidden">
      <SectionTitle
        hint="verified before it is stored · sealed in the local vault"
        action={
          <Button size="sm" variant={adding ? 'ghost' : 'primary'} onClick={() => setAdding(!adding)}>
            {adding ? <XCircle size={11} /> : <Plus size={11} />} {adding ? 'Cancel' : 'Add key'}
          </Button>
        }
      >
        Your API keys
      </SectionTitle>

      {adding && (
        <div className="mx-4 mb-3 space-y-2 rounded-lg border border-line bg-surface-2 p-3">
          <div className="flex gap-2">
            <select aria-label="Key provider"
              value={draft.provider}
              onChange={(e) => setDraft({ ...draft, provider: e.target.value })}
              className="rounded-lg border border-line bg-canvas px-2.5 py-1.5 text-[12px] outline-none focus:border-accent"
            >
              {PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            <input
              value={draft.model}
              onChange={(e) => setDraft({ ...draft, model: e.target.value })}
              placeholder="model, e.g. claude-opus-5"
              className="min-w-0 flex-1 rounded-lg border border-line bg-canvas px-2.5 py-1.5 text-[12px] outline-none placeholder:text-ink-3 focus:border-accent"
            />
          </div>
          {needsEndpoint && (
            <input
              value={draft.base_url}
              onChange={(e) => setDraft({ ...draft, base_url: e.target.value })}
              placeholder="endpoint, e.g. http://localhost:11434/v1"
              className="w-full rounded-lg border border-line bg-canvas px-2.5 py-1.5 text-[12px] outline-none placeholder:text-ink-3 focus:border-accent"
            />
          )}
          <input
            type="password"
            value={draft.api_key}
            onChange={(e) => { setDraft({ ...draft, api_key: e.target.value }); setProbe(null); }}
            placeholder="API key (sealed in the local vault, never sent anywhere else)"
            className="w-full rounded-lg border border-line bg-canvas px-2.5 py-1.5 text-[12px] outline-none placeholder:text-ink-3 focus:border-accent"
          />
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 text-[11px] text-ink-3">
              monthly cap $
              <input
                type="number" min={0} step="1" value={draft.monthly_budget_usd}
                onChange={(e) => setDraft({ ...draft, monthly_budget_usd: e.target.value })}
                placeholder="0 = none"
                className="w-20 rounded-lg border border-line bg-canvas px-2 py-1 text-[11px] outline-none focus:border-accent"
              />
            </label>
            <label className="flex items-center gap-1.5 text-[11px] text-ink-3">
              <input
                type="checkbox" checked={draft.scoped}
                onChange={(e) => setDraft({ ...draft, scoped: e.target.checked })}
                className="accent-ink"
              />
              use only for {project?.key ?? 'this project'} (otherwise it is the default for all)
            </label>
          </div>

          {probe && (
            <p className={clsx('text-[11.5px]', probe.ok ? 'text-pass' : 'text-fail')}>
              {probe.ok ? 'The provider answered. This key works.' : probe.error}
            </p>
          )}
          {error && <p className="text-[11.5px] text-fail">{error}</p>}

          <div className="flex gap-1.5">
            <Button size="sm" variant="ghost" onClick={test} disabled={busy || !draft.api_key}>
              {busy ? <Spinner /> : <CheckCircle2 size={11} />} Test it
            </Button>
            <Button size="sm" variant="primary" onClick={save} disabled={busy || !draft.api_key}>
              Save
            </Button>
          </div>
          <p className="text-[10.5px] leading-relaxed text-ink-3">
            The key is probed against the provider before it is stored. A key that
            does not work is refused here rather than failing in the middle of a
            scheduled run at 2am.
          </p>
        </div>
      )}

      <div className="border-t border-line">
        {keys.length === 0 && (
          <Empty
            icon={<KeyRound size={20} />}
            title="No keys stored"
            body="GaleQEA runs fully without one. Add a key to enable requirement analysis, semantic healing, RCA ranking and model-driven exploration."
          />
        )}
        {keys.map((k) => (
          <div key={`${k.provider}-${k.scope}`} className="border-b border-line/60 px-4 py-2.5 last:border-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[12.5px] font-medium text-ink">{k.provider}</span>
              <Chip tone={k.scope === 'global' ? 'neutral' : 'brand'}>
                {k.scope === 'global' ? 'all projects' : 'this project'}
              </Chip>
              {k.model && <code className="mono text-[10.5px] text-ink-3">{k.model}</code>}
              {k.over_budget && <Chip tone="danger">budget spent</Chip>}
              <code className="mono ml-auto text-[10.5px] text-ink-3">{k.hint}</code>
              <button
                onClick={() => revoke(k.provider, k.scope)}
                className="p-1 text-ink-3 transition-colors hover:text-fail"
                title="Revoke"
              >
                <Trash2 size={11} />
              </button>
            </div>
            <div className="mt-1 flex items-center gap-3 text-[10.5px] text-ink-3">
              <span>
                ${k.spend_this_month} spent this month
                {k.monthly_budget_usd ? ` of $${k.monthly_budget_usd}` : ' · no cap'}
              </span>
              {k.verified_at && <span>· verified when saved</span>}
            </div>
            {k.monthly_budget_usd > 0 && (
              <Meter
                value={(k.spend_this_month / k.monthly_budget_usd) * 100}
                tone={k.over_budget ? 'fail' : 'brand'}
                className="mt-1.5"
              />
            )}
          </div>
        ))}
      </div>

      {usage && usage.calls > 0 && (
        <div className="border-t border-line px-4 py-3">
          <div className="flex items-center gap-2">
            <Coins size={12} className="text-ink-3" />
            <span className="text-[11px] font-medium text-ink-2">
              Last 30 days: ${usage.cost_usd} across {usage.calls} call(s)
            </span>
          </div>
          <div className="mt-1.5 space-y-0.5">
            {Object.entries(usage.by_operation).map(([name, stats]: any) => (
              <div key={name} className="flex items-baseline gap-2 text-[10.5px] text-ink-3">
                <span className="w-40 truncate">{name.replace(/_/g, ' ')}</span>
                <span className="mono">{stats.calls}×</span>
                <span className="mono">{(stats.input_tokens + stats.output_tokens).toLocaleString()} tok</span>
                <span className="mono ml-auto">${stats.cost_usd}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Panel>
  );
}


/** Outbound webhooks: register endpoints, and see the delivery log. Signed with
 *  HMAC-SHA256 (Standard Webhooks); the secret is shown exactly once. */
function WebhooksPanel() {
  const { project } = useApp();
  const [hooks, setHooks] = useState<any[]>([]);
  const [events, setEvents] = useState<string[]>([]);
  const [deliveries, setDeliveries] = useState<any[]>([]);
  const [adding, setAdding] = useState(false);
  const [url, setUrl] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!project) return;
    const list = await api.get<any>(`/api/projects/${project.id}/webhooks`);
    setHooks(list.webhooks);
    setEvents(list.available_events);
    const log = await api.get<any>(`/api/projects/${project.id}/webhooks/deliveries`);
    setDeliveries(log.deliveries);
  }, [project]);
  useEffect(() => { load().catch(() => { /* panel still renders */ }); }, [load]);

  const add = async () => {
    if (!project || !url.trim() || !selected.length) return;
    setBusy(true);
    try {
      const created = await api.post<any>(`/api/projects/${project.id}/webhooks`,
        { url: url.trim(), events: selected });
      setSecret(created.secret);
      setUrl(''); setSelected([]); setAdding(false);
      await load();
    } finally { setBusy(false); }
  };
  const remove = async (id: string) => {
    if (!project) return;
    await api.del(`/api/projects/${project.id}/webhooks/${id}`);
    await load();
  };
  const toggle = (e: string) =>
    setSelected((s) => (s.includes(e) ? s.filter((x) => x !== e) : [...s, e]));

  return (
    <Panel className="overflow-hidden">
      <SectionTitle
        hint="signed HMAC-SHA256 (Standard Webhooks) · secret shown once"
        action={
          <Button size="sm" variant={adding ? 'ghost' : 'primary'} onClick={() => setAdding((v) => !v)}>
            {adding ? <XCircle size={11} /> : <Plus size={11} />} {adding ? 'Cancel' : 'Add webhook'}
          </Button>
        }
      >
        Webhooks
      </SectionTitle>

      {secret && (
        <div className="mx-4 mt-3 rounded-lg border border-flaky/30 bg-flaky/[0.07] p-2.5 text-[11px] leading-relaxed text-flaky">
          Secret (shown once, store it now): <code className="mono break-all">{secret}</code>
        </div>
      )}

      {adding && (
        <div className="mx-4 mt-3 space-y-2 rounded-lg border border-line bg-surface-2 p-3">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://your-endpoint/hook"
            className="w-full rounded-lg border border-line bg-canvas px-2.5 py-1.5 text-[12px] outline-none focus:border-accent"
          />
          <div className="flex flex-wrap gap-1.5">
            {events.map((e) => (
              <button
                key={e}
                onClick={() => toggle(e)}
                className={clsx('rounded-sm border px-2 py-0.5 text-[10.5px] transition',
                  selected.includes(e) ? 'border-accent/40 bg-accent/10 text-accent' : 'border-line text-ink-3 hover:text-ink-2')}
              >
                {e}
              </button>
            ))}
          </div>
          <Button size="sm" variant="primary" onClick={add} disabled={busy || !url.trim() || !selected.length}>
            Create
          </Button>
        </div>
      )}

      <div className="border-t border-line">
        {hooks.length === 0 && (
          <Empty icon={<Plug size={20} />} title="No webhooks"
            body="POST run.finished / run.failed / heal.proposed / approval.requested to an external endpoint." />
        )}
        {hooks.map((h) => (
          <div key={h.id} className="flex items-center gap-2 border-b border-line/60 px-4 py-2.5 last:border-0">
            <Plug size={13} className={h.active ? 'text-pass' : 'text-ink-3'} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-[12px] text-ink">{h.url}</p>
              <p className="truncate text-[10.5px] text-ink-3">{(h.events || []).join(', ')} · secret {h.secret_hint}</p>
            </div>
            <button onClick={() => remove(h.id)} title="Delete" className="p-1 text-ink-3 transition hover:text-fail">
              <Trash2 size={11} />
            </button>
          </div>
        ))}
      </div>

      {deliveries.length > 0 && (
        <div className="border-t border-line px-4 py-2">
          <p className="mb-1 text-[10.5px] uppercase tracking-wide text-ink-3">Recent deliveries</p>
          <div className="max-h-40 space-y-0.5 overflow-y-auto">
            {deliveries.slice(0, 20).map((d) => (
              <div key={d.id} className="flex items-baseline gap-2 text-[10.5px]">
                <Chip tone={d.status === 'delivered' ? 'good' : 'danger'}>{d.status}</Chip>
                <span className="mono text-ink-2">{d.event}</span>
                <span className="text-ink-3">{d.attempts}×{d.response_code ? ` · ${d.response_code}` : ''}</span>
                <span className="ml-auto truncate text-ink-3">{d.error}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Panel>
  );
}
