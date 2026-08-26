import clsx from 'clsx';
import { Wind } from 'lucide-react';

/**
 * The GaleQEA wordmark.
 *
 * Flat by rule. No gradient, no glow, no drop shadow — the two brand colours are
 * laid down as solid fills and nothing else. A neon bloom would also put the
 * mark into direct competition with the status palette, where saturated colour
 * already means something specific (pass, fail, flaky) and must not be spent on
 * decoration.
 *
 * The typographic contrast carries the identity instead: `Gale` italic and
 * lighter, leaning forward; `QEA` upright and heavy, planted. Motion against
 * rigour, which is the product in two words.
 */

/** Brand accent — a flat, modern blue, cohesive with the app's UI accent. */
/** Brand accent — a warm, flat, professional orange against the navy ground. */
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

export function GaleQEALogo({
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
      // announces "Gale" and "QEA" as two unrelated fragments.
      role="img"
      aria-label="GaleQEA"
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
        <span aria-hidden="true" className={clsx('font-sans leading-none', scale.text)}>
          <span className="font-semibold italic tracking-tight text-[#0A192F] dark:text-white">
            Gale
          </span>
          {/* No letter-space between the two halves: they read as one word, with
              the weight and slant change doing the separating. */}
          <span className="font-extrabold not-italic tracking-tight text-[#FFD60A]">
            QEA
          </span>
        </span>
      )}
    </span>
  );
}

export default GaleQEALogo;
