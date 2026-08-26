import type { ReactNode } from 'react';
import { GaleQEALogo } from '../ui/GaleQEALogo';

/**
 * The branded empty state.
 *
 * It says what would put content here and how to make that happen. An empty
 * pane that only says "no data" is indistinguishable from a broken one, and the
 * user has no way to tell which they are looking at.
 */
export function EmptyPane({
  icon, title, body, hint, action,
}: {
  icon: ReactNode;
  title: string;
  body: string;
  hint?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 py-16 text-center">
      <div className="relative mb-5">
        <GaleQEALogo size="lg" showText={false} />
        <span className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-md border border-line bg-surface text-ink-3">
          {icon}
        </span>
      </div>
      <h2 className="text-[15px] font-semibold tracking-tight text-ink">{title}</h2>
      <p className="mt-2 max-w-sm text-[12.5px] leading-relaxed text-ink-3">{body}</p>
      {hint && <p className="mt-3 text-[11.5px] text-ink-3">{hint}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
