import clsx from 'clsx';
import type { ReactNode } from 'react';
import { statusMeta } from '../lib/format';


export function StatusPill({ status, live = false, className }: { status: string; live?: boolean; className?: string }) {
  const meta = statusMeta(status);
  return (
    <span className={clsx(
      'pill inline-flex items-center gap-1.5 border px-2 py-0.5 text-[11px] font-medium',
      meta.bg, meta.color, className,
    )}>
      <span className={clsx('dot h-1.5 w-1.5', meta.dot, live && 'pulse-dot')} />
      {meta.label}
    </span>
  );
}

export function Chip({ children, tone = 'neutral', className }: { children: ReactNode; tone?: 'neutral' | 'brand' | 'warn' | 'danger' | 'good'; className?: string }) {
  const tones = {
    neutral: 'border-line bg-surface-3 text-ink-2',
    brand: 'border-accent/30 bg-accent/10 text-accent',
    warn: 'border-flaky/30 bg-flaky/10 text-flaky',
    danger: 'border-fail/30 bg-fail/10 text-fail',
    good: 'border-pass/30 bg-pass/10 text-pass',
  };
  return (
    <span className={clsx('inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium', tones[tone], className)}>
      {children}
    </span>
  );
}

export function Button({
  children, onClick, variant = 'ghost', size = 'md', disabled, className, type = 'button', title,
}: {
  children: ReactNode; onClick?: () => void;
  variant?: 'primary' | 'ghost' | 'danger' | 'subtle';
  size?: 'sm' | 'md'; disabled?: boolean; className?: string;
  type?: 'button' | 'submit'; title?: string;
}) {
  // The primary action is monochrome - light ground, dark text. It reads as
  // emphasis without spending a colour that status already owns.
  const variants = {
    primary: 'bg-ink text-canvas hover:bg-white border-transparent font-semibold',
    ghost: 'bg-surface-2 text-ink-2 hover:text-ink hover:bg-surface-3 border-line hover:border-line-strong',
    subtle: 'bg-transparent text-ink-3 hover:text-ink hover:bg-surface-2 border-transparent',
    danger: 'bg-transparent text-fail hover:bg-fail/10 border-fail/30',
  };
  return (
    <button
      type={type} title={title} onClick={onClick} disabled={disabled}
      className={clsx(
        'inline-flex items-center justify-center gap-1.5 rounded-lg border font-medium',
        'transition-all duration-150 active:scale-[0.98]',
        'disabled:opacity-40 disabled:pointer-events-none',
        size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-3.5 py-2 text-[13px]',
        variants[variant], className,
      )}
    >
      {children}
    </button>
  );
}

export function Panel({ children, className, glow = false }: { children: ReactNode; className?: string; glow?: boolean }) {
  return <div className={clsx('panel', glow && 'agent-glow', className)}>{children}</div>;
}

export function SectionTitle({ children, hint, action }: { children: ReactNode; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-4 pt-3.5 pb-2">
      <div className="flex min-w-0 items-baseline gap-2.5">
        <h2 className="truncate text-[13px] font-semibold tracking-tight text-ink">{children}</h2>
        {hint && <span className="truncate text-[11px] text-ink-3">{hint}</span>}
      </div>
      {/* The action never shrinks: a clipped "Approve" is worse than a clipped title. */}
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export function Empty({ icon, title, body, action }: { icon?: ReactNode; title: string; body?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      {icon && <div className="text-ink-3">{icon}</div>}
      <div className="space-y-1.5">
        <p className="text-sm font-medium text-ink-2">{title}</p>
        {body && <p className="mx-auto max-w-sm text-xs leading-relaxed text-ink-3">{body}</p>}
      </div>
      {action}
    </div>
  );
}

export function Meter({ value, tone = 'brand', className }: { value: number; tone?: string; className?: string }) {
  const tones: Record<string, string> = {
    brand: 'bg-accent', pass: 'bg-pass', fail: 'bg-fail', flaky: 'bg-flaky',
  };
  return (
    <div className={clsx('h-1.5 w-full overflow-hidden rounded-full bg-surface-3', className)}>
      <div
        className={clsx('h-full rounded-full transition-[width] duration-500 ease-out', tones[tone] ?? tones.brand)}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

export function KeyValue({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="shrink-0 text-[11px] text-ink-3">{label}</span>
      <span className={clsx('truncate text-right text-[12px] text-ink-2', mono && 'mono')}>{value}</span>
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={clsx('animate-spin', className)} viewBox="0 0 24 24" fill="none" width="14" height="14">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.2" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
