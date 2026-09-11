import { useEffect, useRef, useState } from 'react';
import { Command, CornerDownLeft } from 'lucide-react';
import { runCommand } from '../workspace';

/**
 * The ⌘K command palette.
 *
 * The chat is the primary interface, so the palette is a fast path *into* it:
 * every entry dispatches a plain-English command that lands in the transcript as
 * "you: …". Templates with a `<placeholder>` fill the box for editing; complete
 * commands run on Enter. Nothing here does anything the chat can't. It's a menu
 * of the deterministic commands, discoverable without memorising them.
 */

interface Cmd { label: string; template: string; }

const COMMANDS: Cmd[] = [
  { label: 'Test a website', template: 'test https://<url>' },
  { label: 'What\'s next on the journey', template: "what's next" },
  { label: 'Run the smoke tests', template: 'run the smoke tests on staging' },
  { label: 'Re-run only the failures', template: 'rerun only failed' },
  { label: 'Plan coverage', template: 'plan coverage' },
  { label: 'Status brief', template: 'status brief' },
  { label: 'Quality retrospective', template: 'quality retro last 30 days' },
  { label: 'Coverage report', template: 'coverage report as markdown' },
  { label: 'Export the last run', template: 'export last run as junit' },
  { label: 'Copy the run report for AI', template: 'copy the run report for AI' },
  { label: 'Schedule a run', template: 'schedule regression nightly at 2am' },
];

const hasPlaceholder = (t: string) => t.includes('<');

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === 'Escape') {
        setOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (open) { setQ(''); setActive(0); setTimeout(() => inputRef.current?.focus(), 10); }
  }, [open]);

  if (!open) return null;

  const needle = q.trim().toLowerCase();
  const filtered = needle
    ? COMMANDS.filter((c) => c.label.toLowerCase().includes(needle) || c.template.toLowerCase().includes(needle))
    : COMMANDS;

  const dispatch = (text: string) => {
    const t = text.trim();
    if (!t) return;
    runCommand(t);
    setOpen(false);
  };
  const choose = (c: Cmd) => (hasPlaceholder(c.template) ? (setQ(c.template), inputRef.current?.focus()) : dispatch(c.template));

  const onEnter = () => {
    // A typed command with a space (or URL) runs as-is; otherwise take the
    // highlighted suggestion, filling it if it still has a placeholder.
    if (needle && (needle.includes(' ') || needle.includes('://'))) { dispatch(q); return; }
    const pick = filtered[active] ?? filtered[0];
    if (pick) choose(pick);
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/40 pt-[14vh] backdrop-blur-[1px]"
      onClick={() => setOpen(false)}
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <div
        className="w-[min(92vw,560px)] overflow-hidden rounded-xl border border-line bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-line px-3">
          <Command size={14} className="shrink-0 text-ink-3" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => { setQ(e.target.value); setActive(0); }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); onEnter(); }
              else if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, filtered.length - 1)); }
              else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
            }}
            placeholder="Type a command…  test https://…, run smoke, plan coverage"
            className="w-full bg-transparent py-3 text-[13px] text-ink outline-none placeholder:text-ink-3"
          />
          <kbd className="mono shrink-0 rounded border border-line px-1 py-0.5 text-[9.5px] text-ink-3">esc</kbd>
        </div>
        <ul className="max-h-80 overflow-y-auto p-1">
          {filtered.length === 0 && (
            <li className="px-3 py-2 text-[12px] text-ink-3">
              Press <CornerDownLeft size={11} className="inline" /> to send “{q}” to the agent.
            </li>
          )}
          {filtered.map((c, i) => (
            <li key={c.label}>
              <button
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(c)}
                className={clsxLite(i === active)}
              >
                <span>{c.label}</span>
                <span className="mono text-[10.5px] text-ink-3">{c.template}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function clsxLite(activeRow: boolean): string {
  return [
    'flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-[12.5px] transition',
    activeRow ? 'bg-surface-2 text-ink' : 'text-ink-2 hover:bg-surface-2/60 hover:text-ink',
  ].join(' ');
}
