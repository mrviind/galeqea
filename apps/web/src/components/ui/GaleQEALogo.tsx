import clsx from 'clsx';
import { Wind } from 'lucide-react';

/**
 * The GaleQEA wordmark.
 *
 * Flat by rule. No gradient, no glow, no drop shadow: the two brand colours are
 * laid down as solid fills and nothing else. A neon bloom would also put the
 * mark into direct competition with the status palette, where saturated colour
 * already means something specific (pass, fail, flaky) and must not be spent on
 * decoration.
 *
 * The typographic contrast carries the identity instead: `gale` and `a` italic
 * and lighter, leaning forward; `QE` upright, heavy and gold, planted in the
 * middle. The mark spells the exact package name `galeqea`, but the caps make
 * **QE** (Quality Engineering) read clearly inside it. Motion against rigour,
 * which is the product in two letters.
 *
 * `QE`'s gold is theme-aware, same fix as the `pass` status colour: the pure
 * `#FFD60A` only holds contrast on the navy icon box and dark surfaces, so on
 * a white/light ground it uses the deepened `--color-pass` gold instead of
 * washing out.
 */

/** Brand accent, the icon's gold: always on the navy box, never bare on a page
 * background, so it stays the pure, vivid value in both themes. */
export const GALE_ACCENT = '#FFD60A';
/** Deep navy: the geometric ground the mark sits on. */
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
      aria-label="Gale QE Agent"
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
        <span aria-hidden="true" className={clsx('font-sans lowercase leading-none', scale.text)}>
          {/* No letter-space between the parts: they read as one word `galeqea`,
              with the caps + weight + colour change on QE doing the separating. */}
          <span className="font-semibold italic tracking-tight text-[#0A192F] dark:text-white">
            gale
          </span>
          <span className="font-extrabold not-italic uppercase tracking-tight text-pass">
            QE
          </span>
          <span className="font-semibold italic tracking-tight text-[#0A192F] dark:text-white">
            a
          </span>
        </span>
      )}
    </span>
  );
}

export default GaleQEALogo;
