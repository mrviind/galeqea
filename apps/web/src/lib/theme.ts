/**
 * Theme is a `.dark` class on <html>. A tiny inline script in index.html sets it
 * before first paint, so there is no flash, and the top bar toggles it.
 * Dark is the default and the product's identity; light is an opt-in override.
 */
export type Theme = 'dark' | 'light';

const KEY = 'galeqea.theme';

export function getTheme(): Theme {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
}

export function setTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark');
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* private mode / storage disabled; the class still applies for this session */
  }
  // Keep the mobile browser chrome in step with the page.
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', theme === 'dark' ? '#08090d' : '#f6f8fa');
}

export function toggleTheme(): Theme {
  const next: Theme = getTheme() === 'dark' ? 'light' : 'dark';
  setTheme(next);
  return next;
}
