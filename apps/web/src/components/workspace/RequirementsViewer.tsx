import { useMemo } from 'react';
import { ArrowRight, FileText, FileUp, Globe, RefreshCw, Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';
import { renderMarkdown } from '../../lib/markdown';
import { relative } from '../../lib/format';
import { prefillChat, useWorkspace } from '../../workspace';
import { FullFloorPipeline } from './FullFloorPipeline';

/** First-run empty state. Testing a website is an *agentic* action, so the entry
 *  point is the QE Agent (the right dock), not a form on the canvas: this hands the
 *  user straight to the agent with the command seeded. */
function TestAnyWebsite() {
  return (
    <div className="flex h-full min-h-0 flex-col items-center gap-10 overflow-y-auto px-6 py-10">
      <div className="flex w-full max-w-md flex-col items-center gap-5 text-center">
        <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-surface-2 text-accent">
          <Globe size={20} />
        </div>
        <div>
          <h2 className="text-[16px] font-semibold text-ink">Test any website</h2>
          <p className="mx-auto mt-1.5 max-w-sm text-[12.5px] leading-relaxed text-ink-3">
            Ask the QE Agent. It explores the site, proposes a test plan, and (once you
            approve) tests every page in a real browser and reports. Runs on any model.
          </p>
        </div>
        <button
          onClick={() => prefillChat('test ')}
          className="flex items-center gap-1.5 rounded-lg bg-ink px-4 py-2 text-[13px] font-semibold text-canvas transition hover:opacity-90"
        >
          <Sparkles size={14} /> Ask the QE Agent <ArrowRight size={14} />
        </button>
        <div className="flex flex-wrap items-center justify-center gap-1.5">
          <button
            onClick={() => prefillChat('test https://www.aravindarumugam.com')}
            className="flex items-center gap-1.5 rounded-lg border border-line bg-surface-2 px-2.5 py-1.5 text-[11.5px] text-ink-2 transition hover:border-line-strong hover:text-ink"
          >
            <Globe size={12} className="text-ink-3" /> Test aravindarumugam.com
          </button>
          <Link
            to="/requirements"
            className="flex items-center gap-1.5 rounded-lg border border-line bg-surface-2 px-2.5 py-1.5 text-[11.5px] text-ink-2 transition hover:border-line-strong hover:text-ink"
          >
            <FileUp size={12} className="text-ink-3" /> Try the sample requirements
          </Link>
        </div>
        <p className="text-[11.5px] text-ink-3">
          Or type a URL in the QE Agent, e.g. <span className="mono text-ink-2">test https://your-site.com</span>
        </p>
      </div>

      <FullFloorPipeline />
    </div>
  );
}

/** [Requirements]: whatever `query_requirements` last put on the canvas. */
export function RequirementsViewer() {
  const { activeRequirements, clearPane } = useWorkspace();
  const html = useMemo(
    () => (activeRequirements ? renderMarkdown(activeRequirements.markdown) : ''),
    [activeRequirements],
  );

  if (!activeRequirements) {
    return <TestAnyWebsite />;
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
