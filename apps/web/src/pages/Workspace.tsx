import { useEffect, useState } from 'react';
import clsx from 'clsx';
import { Activity, FileText, Grid3x3 } from 'lucide-react';
import { RCATelemetry } from '../components/workspace/RCATelemetry';
import { FOCUS_PANE_EVENT } from '../workspace';
import { RequirementsViewer } from '../components/workspace/RequirementsViewer';
import { TestMatrix } from '../components/workspace/TestMatrix';

/**
 * The QA Grid — the left canvas of the workspace.
 *
 * Tabs rather than routes on purpose. These three viewers are facets of one
 * investigation: a requirement, the scenarios covering it, and why one of them
 * failed. Someone moving between them is following a single thread, and pushing
 * that thread through the browser history would make Back mean something
 * different every time.
 */

const TABS = [
  { id: 'requirements', label: 'Requirements', icon: FileText, render: () => <RequirementsViewer /> },
  { id: 'matrix', label: 'Test Matrix', icon: Grid3x3, render: () => <TestMatrix /> },
  { id: 'rca', label: 'RCA Telemetry', icon: Activity, render: () => <RCATelemetry /> },
] as const;

/** Pane names as the tools use them, mapped to tab ids. */
const PANE_TO_TAB: Record<string, TabId> = {
  requirements: 'requirements',
  test_matrix: 'matrix',
  telemetry: 'rca',
};

type TabId = (typeof TABS)[number]['id'];

export default function Workspace() {
  const [active, setActive] = useState<TabId>('requirements');
  const current = TABS.find((tab) => tab.id === active) ?? TABS[0];

  // A tool that fills a pane brings it to the front. Without this the agent
  // silently populates a tab the user is not looking at, and the work appears
  // to have gone nowhere.
  useEffect(() => {
    const onFocus = (event: Event) => {
      const pane = (event as CustomEvent<string>).detail;
      const tab = PANE_TO_TAB[pane];
      if (tab) setActive(tab);
    };
    window.addEventListener(FOCUS_PANE_EVENT, onFocus);
    return () => window.removeEventListener(FOCUS_PANE_EVENT, onFocus);
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div
        role="tablist"
        aria-label="QA workspace"
        className="flex shrink-0 items-center gap-1 border-b border-line px-3"
      >
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            role="tab"
            id={`tab-${id}`}
            aria-selected={active === id}
            aria-controls={`panel-${id}`}
            onClick={() => setActive(id)}
            className={clsx(
              'flex items-center gap-2 border-b-2 px-3 py-2.5 text-[12.5px] font-medium transition-colors',
              active === id
                ? 'border-accent text-ink'
                : 'border-transparent text-ink-3 hover:text-ink-2',
            )}
          >
            <Icon size={13} />
            {label}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id={`panel-${current.id}`}
        aria-labelledby={`tab-${current.id}`}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        {current.render()}
      </div>
    </div>
  );
}
