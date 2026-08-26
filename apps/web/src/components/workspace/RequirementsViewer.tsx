import { useMemo } from 'react';
import { FileText, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';
import { renderMarkdown } from '../../lib/markdown';
import { relative } from '../../lib/format';
import { useWorkspace } from '../../workspace';
import { EmptyPane } from './EmptyPane';

/** [Requirements] — whatever `query_requirements` last put on the canvas. */
export function RequirementsViewer() {
  const { activeRequirements, clearPane } = useWorkspace();
  const html = useMemo(
    () => (activeRequirements ? renderMarkdown(activeRequirements.markdown) : ''),
    [activeRequirements],
  );

  if (!activeRequirements) {
    return (
      <EmptyPane
        icon={<FileText size={18} />}
        title="Awaiting ingestion"
        body="Ask the Copilot about a feature and its requirements land here. Try “what are the acceptance criteria for checkout?” — the agent queries the project rather than answering from memory."
        hint={<>Nothing ingested yet? <Link to="/requirements" className="text-accent hover:underline">Upload a document</Link>.</>}
      />
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-line px-5 py-2.5">
        <FileText size={14} className="shrink-0 text-ink-3" />
        <h2 className="min-w-0 truncate text-[13px] font-semibold text-ink">{activeRequirements.title}</h2>
        <span className="shrink-0 text-[11px] text-ink-3 tabular-nums">
          {activeRequirements.count} requirement{activeRequirements.count === 1 ? '' : 's'}
        </span>
        <span className="shrink-0 text-[11px] text-ink-3">· {relative(activeRequirements.at)}</span>
        <button
          onClick={() => clearPane('requirements')}
          title="Clear this pane"
          className="ml-auto shrink-0 rounded-md p-1 text-ink-3 transition hover:text-ink"
        >
          <RefreshCw size={12} />
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <article className="prose-doc mx-auto max-w-3xl px-6 py-7" dangerouslySetInnerHTML={{ __html: html }} />
      </div>
    </div>
  );
}
