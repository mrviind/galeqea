import hljs from 'highlight.js/lib/core';
import gherkin from 'highlight.js/lib/languages/gherkin';
import json from 'highlight.js/lib/languages/json';
import typescript from 'highlight.js/lib/languages/typescript';

/**
 * Syntax highlighting, registering only the languages actually rendered.
 *
 * `highlight.js/lib/core` plus three grammars is a fraction of the full
 * bundle, which ships close to two hundred languages QE Agent will never show.
 * The theme comes from the app's own tokens in `styles/index.css`, so a code
 * block stays legible in both themes instead of carrying a stylesheet that
 * assumes one.
 */
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('ts', typescript);
hljs.registerLanguage('gherkin', gherkin);
hljs.registerLanguage('json', json);

const ESCAPES: Record<string, string> = {
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
};

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (c) => ESCAPES[c] ?? c);
}

/** Highlighted HTML, or escaped plain text if the grammar is unknown. */
export function highlight(code: string, language: string): string {
  if (!hljs.getLanguage(language)) return escapeHtml(code);
  try {
    return hljs.highlight(code, { language, ignoreIllegals: true }).value;
  } catch {
    // A grammar that throws must not take the pane down with it.
    return escapeHtml(code);
  }
}
