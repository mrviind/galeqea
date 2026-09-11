import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Info } from 'lucide-react';

/**
 * A small "what does this mean" popover: the (i) badge next to a metric that
 * explains the bands behind a status word like "Strong" or "Needs work".
 *
 * Portaled to <body> and positioned in fixed (viewport) coordinates rather
 * than nested in normal flow: the metric cards it hangs off live inside
 * `overflow-hidden` panels, and an absolutely-positioned child that overflows
 * its own card gets silently clipped by that ancestor otherwise.
 *
 * Click-to-toggle (not hover) so it works the same with touch and keyboard;
 * closes on an outside click, Escape, or any scroll (a fixed-position
 * popover that doesn't track its trigger across a scroll would visually
 * detach from it, which is worse than just closing).
 */
export function InfoPopover({ children, label = 'What does this mean?' }: { children: ReactNode; label?: string }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      const t = e.target as Node;
      if (btnRef.current?.contains(t) || popRef.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    const onScroll = () => setOpen(false);
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    document.addEventListener('scroll', onScroll, true);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('scroll', onScroll, true);
    };
  }, [open]);

  const toggle = () => {
    if (!open && btnRef.current) {
      const r = btnRef.current.getBoundingClientRect();
      setPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) });
    }
    setOpen((v) => !v);
  };

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        aria-label={label}
        aria-expanded={open}
        onClick={toggle}
        className="text-ink-3 transition hover:text-ink-2"
      >
        <Info size={12} />
      </button>
      {open && pos && createPortal(
        <div
          ref={popRef}
          role="dialog"
          style={{ position: 'fixed', top: pos.top, right: pos.right }}
          className="panel z-50 w-56 space-y-1.5 p-2.5 text-left text-[11px] leading-relaxed text-ink-2"
        >
          {children}
        </div>,
        document.body,
      )}
    </>
  );
}

/** One row of a threshold legend inside an InfoPopover: a colour chip, a band
 *  name, and the range it covers, e.g. "Strong · ≥ 80%". */
export function BandRow({ tone, label, range }: { tone: 'pass' | 'flaky' | 'fail'; label: string; range: string }) {
  const dot = { pass: 'bg-pass', flaky: 'bg-flaky', fail: 'bg-fail' }[tone];
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
        {label}
      </span>
      <span className="mono text-ink-3">{range}</span>
    </div>
  );
}
