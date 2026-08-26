import { useEffect, useState } from 'react';
import clsx from 'clsx';
import { Check, ChevronDown, Lock, ShieldCheck, TriangleAlert } from 'lucide-react';
import { api } from '../../lib/api';
import { useApp } from '../../state';

/**
 * Model picker, backed by the server-side vault.
 *
 * Ported from the standalone client, with the one change that matters for an
 * enterprise deployment: it holds no credential. It asks the server which
 * providers are credentialed and receives model *identifiers* only. Selecting
 * one sends the identifier back; the server resolves the sealed key at request
 * time and writes the use into the audit ledger.
 *
 * A model the vault cannot reach is shown disabled with the reason, not hidden.
 * Silently omitting it produces the worst support ticket there is — "the model I
 * pay for isn't in the list" — when the answer is simply that nobody has added
 * the key yet.
 */

export interface ServerModel {
  id: string;
  label: string;
  provider: string;
  context: number | null;
  available: boolean;
  reason: string;
  local: boolean;
  scope: string;
}

export function ModelSelector({
  value, onChange,
}: {
  value: { provider: string; model: string } | null;
  onChange: (choice: { provider: string; model: string }) => void;
}) {
  const { project } = useApp();
  const [models, setModels] = useState<ServerModel[]>([]);
  const [open, setOpen] = useState(false);
  const [aiEnabled, setAiEnabled] = useState(true);

  useEffect(() => {
    if (!project) return;
    let alive = true;
    api
      .get<{ models: ServerModel[]; ai_enabled: boolean; active: { provider: string; model: string } }>(
        `/api/settings/models?project_id=${project.id}`,
      )
      .then((body) => {
        if (!alive) return;
        setModels(body.models);
        setAiEnabled(body.ai_enabled);
        if (!value && body.active?.model) onChange(body.active);
      })
      .catch(() => { /* the dock still works in No-AI mode; the label says so */ });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id]);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener('click', close);
    return () => window.removeEventListener('click', close);
  }, [open]);

  const current = models.find((m) => m.id === value?.model && m.provider === value?.provider);
  const grouped = models.reduce<Record<string, ServerModel[]>>((acc, model) => {
    (acc[model.provider] ??= []).push(model);
    return acc;
  }, {});

  return (
    <div className="relative" onClick={(e) => e.stopPropagation()}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex h-7 max-w-[190px] items-center gap-1.5 rounded-lg border border-line bg-surface-2 px-2 text-[11.5px] text-ink-2 transition hover:border-line-strong hover:text-ink"
      >
        {!aiEnabled && <TriangleAlert size={11} className="shrink-0 text-flaky" />}
        <span className="truncate">
          {!aiEnabled ? 'No-AI mode' : (current?.label ?? value?.model ?? 'Choose a model')}
        </span>
        <ChevronDown size={11} className="shrink-0 text-ink-3" />
      </button>

      {open && (
        <div className="absolute bottom-full z-50 mb-1.5 max-h-[60vh] w-[290px] overflow-y-auto rounded-lg border border-line bg-surface shadow-2xl">
          <p className="flex items-start gap-1.5 border-b border-line px-2.5 py-2 text-[10.5px] leading-relaxed text-ink-3">
            <ShieldCheck size={11} className="mt-px shrink-0 text-pass" />
            Keys stay sealed in the server vault. This list carries identifiers only.
          </p>

          {Object.entries(grouped).map(([provider, entries]) => (
            <div key={provider}>
              <p className="px-2.5 pb-1 pt-2 text-[9.5px] font-semibold uppercase tracking-wider text-ink-3">
                {provider.replace(/_/g, ' ')}
              </p>
              {entries.map((model) => (
                <button
                  key={`${provider}:${model.id}`}
                  disabled={!model.available}
                  title={model.reason || undefined}
                  onClick={() => {
                    onChange({ provider: model.provider, model: model.id });
                    setOpen(false);
                  }}
                  className={clsx(
                    'flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[11.5px] transition',
                    model.available
                      ? 'text-ink-2 hover:bg-surface-2 hover:text-ink'
                      : 'cursor-not-allowed text-ink-3/60',
                  )}
                >
                  {model.available ? (
                    <Check
                      size={11}
                      className={clsx(
                        'shrink-0 text-accent',
                        value?.model === model.id && value?.provider === provider ? 'opacity-100' : 'opacity-0',
                      )}
                    />
                  ) : (
                    <Lock size={10} className="shrink-0" />
                  )}
                  <span className="min-w-0 flex-1 truncate">{model.label}</span>
                  {model.context ? (
                    <span className="shrink-0 text-[9.5px] tabular-nums text-ink-3">
                      {Math.round(model.context / 1000)}k
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
          ))}

          {models.length === 0 && (
            <p className="px-2.5 py-3 text-[11px] leading-relaxed text-ink-3">
              No providers are credentialed. Add a key in Settings — it is sealed in the
              vault on this server, never sent to the browser.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
