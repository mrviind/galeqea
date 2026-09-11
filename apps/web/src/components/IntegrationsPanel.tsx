import { useEffect, useState } from 'react';
import { CheckCircle2, ChevronDown, Plug, XCircle } from 'lucide-react';
import { api } from '../lib/api';
import { useApp } from '../state';
import { Button, Chip, Panel, SectionTitle, Spinner } from './primitives';

type Provider = {
  provider: string; label: string; help: string;
  config: string[]; optional?: string[]; secrets: string[];
};

/** Settings → Integrations: connect / verify an external system. Secrets are
 *  typed into password fields, POSTed straight to the sealed vault, and never
 *  echoed back. The API only ever returns a masked hint. */
export function IntegrationsPanel({ initialOpen }: { initialOpen?: string }) {
  const { project } = useApp();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [connections, setConnections] = useState<any[]>([]);
  const [open, setOpen] = useState<string | null>(initialOpen ?? null);

  const load = () => {
    if (!project) return;
    api.get<any>(`/api/projects/${project.id}/integrations/providers`)
      .then((p) => setProviders(p.providers)).catch(() => {});
    api.get<any[]>(`/api/projects/${project.id}/integrations`)
      .then(setConnections).catch(() => {});
  };
  useEffect(load, [project]);
  useEffect(() => { if (initialOpen) setOpen(initialOpen); }, [initialOpen]);

  return (
    <Panel className="overflow-hidden">
      <SectionTitle hint="credentials are sealed in the vault and never returned by the API">
        Integrations
      </SectionTitle>
      <div className="border-t border-line">
        {providers.map((p) => {
          const connection = connections.find((c) => c.provider === p.provider);
          const isOpen = open === p.provider;
          return (
            <div key={p.provider} className="border-b border-line/60 last:border-0">
              <button
                onClick={() => setOpen(isOpen ? null : p.provider)}
                className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-surface-2/50"
              >
                <Plug size={13} className={connection ? 'text-pass' : 'text-ink-3'} />
                <div className="min-w-0 flex-1">
                  <p className="text-[12.5px] text-ink">{p.label}</p>
                  <p className="truncate text-[10.5px] text-ink-3">{p.help}</p>
                </div>
                {connection
                  ? <Chip tone={connection.status === 'connected' ? 'good' : 'warn'}>{connection.status}</Chip>
                  : <Chip>not connected</Chip>}
                <ChevronDown size={13} className={`text-ink-3 transition ${isOpen ? 'rotate-180' : ''}`} />
              </button>
              {isOpen && (
                <IntegrationForm
                  provider={p} projectId={project!.id} connection={connection}
                  onChange={load} />
              )}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

/** A standalone connect form for one provider, used by the chat "connect jira"
 *  block so the secure token field lives in the conversation without the token
 *  ever entering the transcript. */
export function ConnectForm({ providerName, baseUrl }: { providerName: string; baseUrl?: string }) {
  const { project } = useApp();
  const [spec, setSpec] = useState<Provider | null>(null);
  const [connection, setConnection] = useState<any>(null);

  const load = () => {
    if (!project) return;
    api.get<any>(`/api/projects/${project.id}/integrations/providers`)
      .then((p) => setSpec((p.providers as Provider[]).find((x) => x.provider === providerName) ?? null))
      .catch(() => {});
    api.get<any[]>(`/api/projects/${project.id}/integrations`)
      .then((cs) => setConnection(cs.find((c) => c.provider === providerName)))
      .catch(() => {});
  };
  useEffect(load, [project, providerName]);

  if (!project || !spec) return null;
  const seeded = baseUrl ? { ...(connection ?? {}), config: { ...(connection?.config ?? {}), base_url: baseUrl } } : connection;
  return (
    <div className="overflow-hidden rounded-lg border border-line">
      <div className="border-b border-line bg-surface-2/50 px-3 py-1.5 text-[11.5px] font-medium text-ink-2">
        Connect {spec.label}
      </div>
      <IntegrationForm provider={spec} projectId={project.id} connection={seeded} onChange={load} />
    </div>
  );
}

function IntegrationForm({ provider, projectId, connection, onChange }: {
  provider: Provider; projectId: string; connection: any; onChange: () => void;
}) {
  const [config, setConfig] = useState<Record<string, string>>(() => ({ ...(connection?.config ?? {}) }));
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [verify, setVerify] = useState<any>(null);

  const set = (obj: 'c' | 's', k: string, v: string) =>
    (obj === 'c' ? setConfig : setSecrets)((prev) => ({ ...prev, [k]: v }));

  const connect = async () => {
    setBusy(true); setMsg(''); setVerify(null);
    try {
      await api.post(`/api/projects/${projectId}/integrations`, {
        provider: provider.provider, config, secrets,
      });
      setSecrets({}); setMsg('Connected. Credentials sealed in the vault. Now verify.');
      onChange();
    } catch (e: any) { setMsg(e?.message || 'could not connect'); }
    finally { setBusy(false); }
  };

  const runVerify = async () => {
    setBusy(true); setMsg(''); setVerify(null);
    try {
      setVerify(await api.post(`/api/projects/${projectId}/integrations/${provider.provider}/verify`, {}));
    } catch (e: any) { setMsg(e?.message || 'verification failed'); }
    finally { setBusy(false); }
  };

  const isSecret = (name: string) => /token|secret|key|password|api_key/i.test(name);
  const fields = [...provider.config, ...(provider.optional ?? [])];

  return (
    <div className="space-y-2.5 border-t border-line/60 bg-surface-2/30 px-4 py-3">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {fields.map((k) => (
          <label key={k} className="flex flex-col gap-1">
            <span className="text-[10.5px] text-ink-3">
              {k}{provider.optional?.includes(k) ? ' (optional)' : ''}
            </span>
            <input
              value={config[k] ?? ''} onChange={(e) => set('c', k, e.target.value)}
              placeholder={k === 'base_url' ? 'https://your-site.atlassian.net' : ''}
              className="rounded border border-line bg-surface px-2 py-1.5 text-[12px] text-ink outline-none focus:border-accent" />
          </label>
        ))}
        {provider.secrets.map((k) => (
          <label key={k} className="flex flex-col gap-1">
            <span className="text-[10.5px] text-ink-3">
              {k} {connection?.secrets?.includes(k) && <span className="text-pass">· set</span>}
            </span>
            <input
              type="password" autoComplete="off" value={secrets[k] ?? ''}
              onChange={(e) => set('s', k, e.target.value)}
              placeholder={connection?.secrets?.includes(k) ? '•••• (leave blank to keep)' : 'paste secret'}
              className="rounded mono border border-line bg-surface px-2 py-1.5 text-[12px] text-ink outline-none focus:border-accent" />
          </label>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="primary" disabled={busy} onClick={connect}>
          {busy ? <Spinner /> : null} {connection ? 'Update' : 'Connect'}
        </Button>
        <Button size="sm" variant="ghost" disabled={busy || !connection} onClick={runVerify}>
          Verify
        </Button>
      </div>
      {['slack', 'teams'].includes(provider.provider) && connection && (
        <div className="flex flex-wrap items-center gap-2 border-t border-line/60 pt-2">
          <Button size="sm" variant="ghost" disabled={busy} onClick={async () => {
            setBusy(true); setMsg('');
            try { await api.post(`/api/projects/${projectId}/integrations/${provider.provider}/test`, {});
              setMsg('Sent a test message. Check the channel.'); }
            catch (e: any) { setMsg(e?.message || 'test failed'); }
            finally { setBusy(false); }
          }}>Send test</Button>
          <label className="flex items-center gap-1.5 text-[11px] text-ink-2">
            <input type="checkbox" defaultChecked={!!connection.config?.only_failures}
              onChange={async (e) => {
                await api.patch(`/api/projects/${projectId}/integrations/${provider.provider}/events`,
                  { only_failures: e.target.checked });
                onChange();
              }} />
            failures only
          </label>
        </div>
      )}
      {msg && <p className="text-[11px] text-ink-2">{msg}</p>}
      {verify && (
        <div className="rounded-md border border-pass/25 bg-pass/[0.06] p-2.5 text-[11.5px]">
          <div className="flex items-center gap-1.5 text-pass">
            {verify.ok ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
            <span className="font-medium">
              {verify.account ? `Connected as ${verify.account}` : 'Verified'}
              {verify.email ? ` (${verify.email})` : ''}
            </span>
          </div>
          {Array.isArray(verify.projects) && verify.projects.length > 0 && (
            <p className="mt-1 text-ink-2">
              Projects: {verify.projects.map((p: any) => `${p.key}`).join(', ')}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
