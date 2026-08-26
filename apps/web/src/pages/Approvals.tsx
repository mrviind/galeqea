import { useCallback, useEffect, useState } from 'react';
import clsx from 'clsx';
import { Bot, Check, ShieldCheck, User, X } from 'lucide-react';
import { api } from '../lib/api';
import type { Approval } from '../lib/api';
import { RISK_COLOR, relative } from '../lib/format';
import { useApp, useEvents } from '../state';
import { Button, Chip, Empty, Panel, SectionTitle } from '../components/primitives';

export default function Approvals() {
  const { project, refreshOverview } = useApp();
  const [items, setItems] = useState<Approval[]>([]);
  const [status, setStatus] = useState<'pending' | 'applied' | 'rejected'>('pending');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [comment, setComment] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    if (!project) return;
    setItems(await api.get<Approval[]>(`/api/projects/${project.id}/approvals?status=${status}`));
  }, [project, status]);

  useEffect(() => { void load(); }, [load]);
  useEvents(['approval.requested', 'approval.decided'], () => void load());

  const decide = async (item: Approval, decision: 'approve' | 'reject') => {
    if (!project) return;
    setBusy(item.id); setError('');
    try {
      await api.post(`/api/projects/${project.id}/approvals/${item.id}/decide`, {
        decision, comment: comment[item.id] ?? '',
      });
      await load();
      void refreshOverview();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'could not record that decision');
    } finally { setBusy(null); }
  };

  return (
    <div className="p-3">
      <Panel className="overflow-hidden">
        <SectionTitle
          hint="the AI can never approve its own output"
          action={
            <div className="flex gap-1">
              {(['pending', 'applied', 'rejected'] as const).map((value) => (
                <button
                  key={value}
                  onClick={() => setStatus(value)}
                  className={clsx(
                    'px-2 py-1 text-[11.5px] transition',
                    status === value ? 'bg-surface-3 text-ink' : 'text-ink-3 hover:text-ink-2',
                  )}
                >
                  {value}
                </button>
              ))}
            </div>
          }
        >
          Approval queue
        </SectionTitle>

        {error && (
          <p className="rounded-lg mx-4 mb-2 border border-fail/25 bg-fail/[0.07] px-2.5 py-1.5 text-[11.5px] text-fail">
            {error}
          </p>
        )}

        <div className="border-t border-line">
          {items.length === 0 && (
            <Empty
              icon={<ShieldCheck size={22} />}
              title={status === 'pending' ? 'Nothing waiting for you' : `No ${status} items`}
              body={status === 'pending'
                ? 'Every change an agent proposes lands here first. Nothing reaches the platform until you decide.'
                : undefined}
            />
          )}

          {items.map((item) => (
            <article key={item.id} className="border-b border-line/60 p-4 last:border-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className={clsx('border px-1.5 py-0.5 text-[10px] font-medium', RISK_COLOR[item.risk] ?? RISK_COLOR.medium)}>
                  {item.risk} risk
                </span>
                <code className="mono text-[11px] text-ink-3">{item.action}</code>
                <span className="flex items-center gap-1 text-[11px] text-ink-3">
                  {item.requested_by_kind === 'agent' ? <Bot size={11} /> : <User size={11} />}
                  {item.agent_role || item.requested_by_kind}
                </span>
                <span className="text-[11px] text-ink-3">· {relative(item.created_at)}</span>
                <span className="ml-auto text-[10.5px] text-ink-3">requires {item.required_role}</span>
              </div>

              <h3 className="mt-1.5 text-[13px] font-medium text-ink">{item.title}</h3>
              {item.summary && (
                <p className="mt-1 text-[12px] leading-relaxed text-ink-2">{item.summary}</p>
              )}

              {item.payload?.arguments && (
                <pre className="rounded-md mono mt-2 max-h-44 overflow-auto border border-line bg-canvas p-2.5 text-[10.5px] leading-relaxed text-ink-3">
                  {JSON.stringify(item.payload.arguments, null, 2)}
                </pre>
              )}

              {status === 'pending' && (
                <div className="mt-2.5 flex flex-wrap items-center gap-2">
                  <input
                    value={comment[item.id] ?? ''}
                    onChange={(e) => setComment({ ...comment, [item.id]: e.target.value })}
                    placeholder="Reason (recorded in the audit ledger)…"
                    className="rounded-lg min-w-0 flex-1 border border-line bg-surface-2 px-2.5 py-1.5 text-[12px] outline-none transition placeholder:text-ink-3 focus:border-accent/40"
                  />
                  <Button variant="primary" size="sm" onClick={() => decide(item, 'approve')} disabled={busy === item.id}>
                    <Check size={11} /> Approve & apply
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => decide(item, 'reject')} disabled={busy === item.id}>
                    <X size={11} /> Reject
                  </Button>
                </div>
              )}
            </article>
          ))}
        </div>
      </Panel>
    </div>
  );
}
