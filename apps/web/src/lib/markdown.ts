import { escapeHtml } from './highlight';

/**
 * A small Markdown renderer for the Requirements pane.
 *
 * Deliberately not a library. The pane renders one known producer — the
 * `query_requirements` tool — which emits headings, paragraphs, ordered and
 * unordered lists, blockquotes, inline code and bold. Pulling in a full parser
 * plus a sanitiser for that subset costs more bundle than it saves in code, and
 * every feature it adds is a feature nobody validates.
 *
 * Input is escaped *first* and inline markup applied to the escaped text, so
 * nothing in a requirement document can inject markup. A requirement whose
 * prose contains `<script>` renders as those characters, which is what a
 * reviewer needs to see.
 */

type Block = { html: string };

export function renderMarkdown(source: string): string {
  const lines = source.replace(/\r\n/g, '\n').split('\n');
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let quote: string[] = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    blocks.push({ html: `<p>${inline(paragraph.join(' '))}</p>` });
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    const tag = list.ordered ? 'ol' : 'ul';
    blocks.push({ html: `<${tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join('')}</${tag}>` });
    list = null;
  };
  const flushQuote = () => {
    if (!quote.length) return;
    blocks.push({ html: `<blockquote>${inline(quote.join(' '))}</blockquote>` });
    quote = [];
  };
  const flushAll = () => { flushParagraph(); flushList(); flushQuote(); };

  for (const raw of lines) {
    const line = raw.trimEnd();

    if (!line.trim()) { flushAll(); continue; }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      flushAll();
      const level = heading[1]!.length;
      blocks.push({ html: `<h${level}>${inline(heading[2]!)}</h${level}>` });
      continue;
    }

    const blockquote = /^>\s?(.*)$/.exec(line);
    if (blockquote) { flushParagraph(); flushList(); quote.push(blockquote[1]!); continue; }

    const ordered = /^\s*(\d+)\.\s+(.*)$/.exec(line);
    if (ordered) {
      flushParagraph(); flushQuote();
      if (!list?.ordered) { flushList(); list = { ordered: true, items: [] }; }
      list.items.push(ordered[2]!);
      continue;
    }

    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (bullet) {
      flushParagraph(); flushQuote();
      if (list?.ordered !== false) { flushList(); list = { ordered: false, items: [] }; }
      list.items.push(bullet[1]!);
      continue;
    }

    if (/^(---|\*\*\*|___)\s*$/.test(line)) { flushAll(); blocks.push({ html: '<hr />' }); continue; }

    flushList(); flushQuote();
    paragraph.push(line.trim());
  }
  flushAll();

  return blocks.map((b) => b.html).join('\n');
}

/** Inline markup, applied to already-escaped text so markup cannot be injected. */
function inline(text: string): string {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
}
