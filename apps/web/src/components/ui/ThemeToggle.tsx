import { useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import { getTheme, toggleTheme, type Theme } from '../../lib/theme';

/** Top-bar light/dark switch. Shows the icon for the mode you'd switch *to*. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => getTheme());
  const dark = theme === 'dark';
  return (
    <button
      onClick={() => setTheme(toggleTheme())}
      title={dark ? 'Switch to light mode' : 'Switch to dark mode'}
      aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
      className="flex h-7 w-7 items-center justify-center rounded-lg border border-line bg-surface-2 text-ink-2 transition hover:border-line-strong hover:text-ink"
    >
      {dark ? <Sun size={13} /> : <Moon size={13} />}
    </button>
  );
}
