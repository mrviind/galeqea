import { useEffect, useRef } from 'react';
import clsx from 'clsx';
import { ArrowUp, Loader2, Terminal } from 'lucide-react';
import { ModelSelector } from './ModelSelector';

/**
 * The Assistant composer.
 *
 * The command preview above the box is the part worth keeping: before anything
 * is sent, the server says how it *would* route the sentence — deterministically
 * or through a model — so the user knows whether they are about to spend a token
 * or not. On a platform whose default mode is No-AI, that is not a detail.
 */
export function MessageInput({
  value, onChange, onSend, busy, disabled, preview, model, onModelChange,
}: {
  value: string;
  onChange: (text: string) => void;
  onSend: () => void;
  busy: boolean;
  disabled: boolean;
  preview: { intent: string; explanation: string; path: string } | null;
  model: { provider: string; model: string } | null;
  onModelChange: (choice: { provider: string; model: string }) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    // Floor at one comfortable line. scrollHeight can read 0/too-small when the
    // effect runs before layout or web fonts settle; without a floor the box
    // collapses and the placeholder clips against the top border. The CSS
    // min-height below is the real guard — this keeps the grown height sane.
    el.style.height = `${Math.max(40, Math.min(el.scrollHeight, 160))}px`;
  }, [value]);

  return (
    <div className="shrink-0 border-t border-line px-3 py-2.5">
      {preview && (
        <div className="mb-2 flex items-start gap-2 rounded-lg border border-line bg-surface-2 px-2.5 py-1.5">
          <Terminal size={11} className="mt-0.5 shrink-0 text-ink-3" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-[11px] font-medium text-ink-2">{preview.intent}</p>
            <p className="truncate text-[10.5px] text-ink-3">{preview.explanation}</p>
          </div>
          <span
            className={clsx(
              'shrink-0 rounded-md border px-1.5 py-px text-[9.5px] font-medium',
              preview.path === 'deterministic'
                ? 'border-pass/30 bg-pass/10 text-pass'
                : 'border-line bg-surface-3 text-ink-3',
            )}
            title={preview.path === 'deterministic'
              ? 'Resolved by rule — no model call, no tokens spent'
              : 'Needs a model'}
          >
            {preview.path === 'deterministic' ? 'no model' : preview.path}
          </span>
        </div>
      )}

      <div className="rounded-lg border border-line bg-surface-2 transition focus-within:border-line-strong">
        <textarea
          ref={ref}
          rows={1}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends, Shift+Enter is a newline. Composition must not be
            // interrupted or every CJK candidate selection fires a message.
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              onSend();
            }
          }}
          placeholder={disabled ? 'Select a project first' : 'Ask, or tell QE Agent what to run…'}
          className="block max-h-[160px] min-h-[40px] w-full resize-none bg-transparent px-3 py-2.5 text-[12.5px] leading-normal text-ink placeholder:text-ink-3 focus:outline-none disabled:cursor-not-allowed"
        />
        <div className="flex items-center gap-2 border-t border-line px-1.5 py-1.5">
          <ModelSelector value={model} onChange={onModelChange} />
          <button
            onClick={onSend}
            disabled={busy || disabled || !value.trim()}
            title="Send"
            className="ml-auto flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-ink text-canvas transition hover:bg-white disabled:pointer-events-none disabled:opacity-40"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <ArrowUp size={14} />}
          </button>
        </div>
      </div>
    </div>
  );
}
