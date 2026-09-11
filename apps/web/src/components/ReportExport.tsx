import { useState } from 'react';
import { Check, ChevronDown, Copy, Download } from 'lucide-react';
import { Button } from './primitives';

/**
 * Export controls for any report that exists at `{base}/report.{json,md,junit.xml}`.
 *
 * Two affordances the manager's contract asks for on every report:
 *  - **Export ▾** downloads the report as JSON, Markdown or (runs only) JUnit XML.
 *  - **Copy for AI** puts the Markdown rendering on the clipboard, the fastest
 *    path from "a run finished" to "paste it into a model".
 *
 * Downloads use a Blob object URL, which works because this is the app served on
 * the user's own origin (not a sandboxed artifact).
 */

async function fetchText(url: string): Promise<string> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`report fetch failed: ${res.status}`);
  return res.text();
}

function download(filename: string, text: string, mime: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function downloadBinary(filename: string, url: string): Promise<void> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`report fetch failed: ${res.status}`);
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}

/** The Markdown-to-clipboard button, standalone. */
export function CopyForAI({ base }: { base: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(await fetchText(`${base}/report.md`));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable outside a secure context; Export ▾ still works */
    }
  };
  return (
    <Button size="sm" variant="ghost" onClick={copy}>
      {copied ? <Check size={11} /> : <Copy size={11} />} {copied ? 'Copied' : 'Copy for AI'}
    </Button>
  );
}

export function ReportExport({ base, name, junit = false, word = false, excel = false }: { base: string; name: string; junit?: boolean; word?: boolean; excel?: boolean }) {
  const [open, setOpen] = useState(false);

  const item = (label: string, run: () => void) => (
    <button
      onClick={() => { run(); setOpen(false); }}
      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] text-ink-2 transition hover:bg-surface-3 hover:text-ink"
    >
      {label}
    </button>
  );

  return (
    <div className="relative flex items-center gap-1.5">
      <CopyForAI base={base} />
      <Button size="sm" variant="ghost" onClick={() => setOpen((v) => !v)}>
        <Download size={11} /> Export <ChevronDown size={10} />
      </Button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} aria-hidden="true" />
          <div className="absolute right-0 top-full z-20 mt-1 w-40 rounded-lg border border-line bg-surface-2 p-1 shadow-lg">
            {item('JSON', async () => download(`${name}.json`, await fetchText(`${base}/report.json`), 'application/json'))}
            {item('Markdown', async () => download(`${name}.md`, await fetchText(`${base}/report.md`), 'text/markdown'))}
            {junit && item('JUnit XML', async () => download(`${name}.junit.xml`, await fetchText(`${base}/report.junit.xml`), 'application/xml'))}
            {word && item('Word report (.docx)', () => downloadBinary(`${name}.docx`, `${base}/report.docx`))}
            {excel && item('Excel report (.xlsx)', () => downloadBinary(`${name}.xlsx`, `${base}/report.xlsx`))}
            {item('Copy Markdown', async () => {
              try { await navigator.clipboard.writeText(await fetchText(`${base}/report.md`)); } catch { /* ignore */ }
            })}
          </div>
        </>
      )}
    </div>
  );
}
