import clsx from 'clsx';
import { Wind } from 'lucide-react';

/**
 * The QE Agent wordmark.
 *
 * Flat by rule. No gradient, no glow, no drop shadow — the two brand colours are
 * laid down as solid fills and nothing else. A neon bloom would also put the
 * mark into direct competition with the status palette, where saturated colour
 * already means something specific (pass, fail, flaky) and must not be spent on
 * decoration.
 *
 * `QE` is set in the navy ground colour, `Agent` in the accent, so the eye lands
 * on what the product *is* — an agent — while the discipline (quality engineering)
 * grounds it.
 */

/** Brand accent — a warm, flat golden yellow against the navy ground. */
export const GALE_ACCENT = '#FFD60A';
/** Deep navy — the geometric ground the mark sits on. */
export const GALE_NAVY = '#0A192F';

export type LogoSize = 'sm' | 'md' | 'lg';

const SIZES: Record<LogoSize, { box: string; icon: number; text: string; gap: string }> = {
  sm: { box: 'h-6 w-6 rounded-[5px]', icon: 13, text: 'text-[13px]', gap: 'gap-1.5' },
  md: { box: 'h-7 w-7 rounded-md', icon: 15, text: 'text-[15px]', gap: 'gap-2' },
  lg: { box: 'h-10 w-10 rounded-lg', icon: 21, text: 'text-[22px]', gap: 'gap-2.5' },
};

export function QEAgentLogo({
  size = 'md',
  showText = true,
  className,
}: {
  size?: LogoSize;
  showText?: boolean;
  className?: string;
}) {
  const scale = SIZES[size];

  return (
    <span
      className={clsx('inline-flex select-none items-center', scale.gap, className)}
      // One accessible name for the whole lockup. Without this a screen reader
      // announces "QE" and "Agent" as two unrelated fragments.
      role="img"
      aria-label="QE Agent"
    >
      <span
        aria-hidden="true"
        className={clsx(
          'inline-flex shrink-0 items-center justify-center bg-[#0A192F] dark:bg-slate-800',
          scale.box,
        )}
      >
        <Wind size={scale.icon} color={GALE_ACCENT} strokeWidth={2.25} aria-hidden="true" />
      </span>

      {showText && (
        <span aria-hidden="true" className={clsx('font-sans leading-none tracking-tight', scale.text)}>
          <span className="font-extrabold text-[#0A192F] dark:text-white">QE</span>
          {/* A subtle gap so the two-word name reads as "QE Agent", with the
              accent half — "Agent" — being what the eye keeps. */}
          <span className="ml-[0.16em] font-extrabold text-[#FFD60A]">Agent</span>
        </span>
      )}
    </span>
  );
}

export default QEAgentLogo;
