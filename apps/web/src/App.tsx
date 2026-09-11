import { Suspense, lazy, useEffect, useState } from 'react';
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import clsx from 'clsx';
import {
  Activity, AlertTriangle, BrainCircuit, FileText, Gauge, LayoutGrid, MessagesSquare, MousePointerClick,
  PlayCircle, Rocket, Settings as SettingsIcon, ShieldCheck, TestTube2, Wifi, WifiOff, X,
} from 'lucide-react';
import { useApp } from './state';
import { AgentCopilot } from './components/copilot/AgentCopilot';
import { CommandPalette } from './components/CommandPalette';
import { VersionWatcher } from './components/VersionWatcher';
import { SessionControl } from './components/AuthGate';
import { Spinner } from './components/primitives';
import { GaleQEALogo } from './components/ui/GaleQEALogo';
import { ThemeToggle } from './components/ui/ThemeToggle';
import Workspace from './pages/Workspace';
import { WorkspaceProvider, OPEN_CHAT_EVENT } from './workspace';
// Workspace is the default route, eager for instant first paint. Every other
// page is split into its own chunk and loaded on demand, keeping the initial
// bundle small (see scripts/check-bundle.mjs for the budget guard).
const Command = lazy(() => import('./pages/Command'));
const Runs = lazy(() => import('./pages/Runs'));
const Releases = lazy(() => import('./pages/Releases'));
const RunDetail = lazy(() => import('./pages/RunDetail'));
const Tests = lazy(() => import('./pages/Tests'));
const Requirements = lazy(() => import('./pages/Requirements'));
const Author = lazy(() => import('./pages/Author'));
const Approvals = lazy(() => import('./pages/Approvals'));
const Intelligence = lazy(() => import('./pages/Intelligence'));
const Settings = lazy(() => import('./pages/Settings'));

const NAV_TOP = { to: '/', label: 'Workspace', icon: LayoutGrid, end: true };

// Grouped by workflow stage rather than one flat list: a section label costs
// one row and buys a reader a map of what's an authoring tool vs. an execution
// view vs. a review surface, instead of ten equally-weighted icons.
const NAV_GROUPS: { label: string; items: { to: string; label: string; icon: typeof FileText; badgeKey?: 'approvals_pending' }[] }[] = [
  {
    label: 'Plan & author',
    items: [
      { to: '/requirements', label: 'Requirements', icon: FileText },
      { to: '/author', label: 'Author', icon: MousePointerClick },
      { to: '/tests', label: 'Tests', icon: TestTube2 },
    ],
  },
  {
    label: 'Execute',
    items: [
      { to: '/command', label: 'Command', icon: Gauge },
      { to: '/runs', label: 'Runs', icon: PlayCircle },
      { to: '/releases', label: 'Releases', icon: Rocket },
    ],
  },
  {
    label: 'Review',
    items: [
      { to: '/approvals', label: 'Approvals', icon: ShieldCheck, badgeKey: 'approvals_pending' },
      { to: '/intelligence', label: 'Intelligence', icon: BrainCircuit },
    ],
  },
];

const NAV_BOTTOM = { to: '/settings', label: 'Settings', icon: SettingsIcon };

// A page identity strip, derived from the same nav items rather than a second
// list to keep in sync. Exact-path lookup only: "/" (Workspace, which already
// has its own tab identity) and "/runs/:id" (which gets its own dynamic "Run
// #N" title) are deliberately absent, so they render no generic header.
const PAGE_META: Record<string, { label: string; icon: typeof FileText }> = Object.fromEntries(
  [...NAV_GROUPS.flatMap((g) => g.items), NAV_BOTTOM].map((item) => [item.to, item]),
);

export default function App() {
  const { project, projects, selectProject, overview, connected, loading, error, capabilities } = useApp();
  const location = useLocation();
  const pageMeta = PAGE_META[location.pathname];
  // Below `lg` the right dock can't sit beside the canvas, so it becomes a
  // slide-in drawer instead of vanishing. This holds whether it's open.
  const [chatOpen, setChatOpen] = useState(false);

  // The "test any website" field (and other canvas triggers) can pop the agent
  // open on narrow screens where it lives in a drawer.
  useEffect(() => {
    const open = () => setChatOpen(true);
    window.addEventListener(OPEN_CHAT_EVENT, open);
    return () => window.removeEventListener(OPEN_CHAT_EVENT, open);
  }, []);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center gap-3 text-ink-3">
        <Spinner className="text-accent" />
        <span className="text-sm">Starting GaleQEA…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="panel max-w-md space-y-3 p-6">
          <div className="flex items-center gap-2 text-fail">
            <AlertTriangle size={18} />
            <h1 className="text-sm font-semibold">Cannot reach the GaleQEA API</h1>
          </div>
          <p className="text-xs leading-relaxed text-ink-2">{error}</p>
          <p className="text-xs leading-relaxed text-ink-3">
            Start it with <code className="rounded-md mono bg-surface-3 px-1.5 py-0.5">galeqea up</code>,
            or run the API directly on port 8080.
          </p>
        </div>
      </div>
    );
  }

  const pending = overview?.approvals_pending ?? 0;
  const aiMode = capabilities?.ai_modes?.find((m: any) => m.default)?.mode;

  return (
    <WorkspaceProvider>
    <CommandPalette />
    <VersionWatcher />
    <div className="rounded-lg flex h-full flex-col bg-canvas">
      {/* ---- top bar --------------------------------------------------- */}
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line px-3">
        <div className="flex items-center pr-1">
          <GaleQEALogo size="sm" />
        </div>

        <div className="h-4 w-px bg-line" />

        <select aria-label="Select project"
          value={project?.id ?? ''}
          onChange={(e) => selectProject(e.target.value)}
          className="rounded-lg border border-line bg-surface-2 px-2 py-1 text-[12px] text-ink-2 outline-none transition hover:text-ink focus:border-accent"
        >
          {projects.map((p) => (
            <option key={p.id} value={p.id}>{p.key} · {p.name}</option>
          ))}
        </select>

        <div className="ml-auto flex items-center gap-2.5">
          <ModeBadge />
          <span
            title={connected ? 'Live event stream connected' : 'Reconnecting to the event stream'}
            className={clsx(
              // Deliberately not green: green means *passed* in this product, and
              // spending it on "the websocket is up" makes the real signal weaker.
              'flex items-center gap-1.5 rounded-lg border px-2 py-1 text-[11px]',
              connected
                ? 'border-line bg-surface-2 text-ink-3'
                : 'border-flaky/30 bg-flaky/10 text-flaky',
            )}
          >
            {connected ? <Wifi size={12} /> : <WifiOff size={12} />}
            {connected ? 'Live' : 'Reconnecting'}
          </span>
          <ThemeToggle />
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* ---- left rail ----------------------------------------------- */}
        <nav className="flex min-h-0 w-[188px] shrink-0 flex-col gap-3 overflow-y-auto border-r border-line p-2">
          <NavRow item={NAV_TOP} badges={{}} />
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="space-y-0.5">
              <p className="px-2.5 pb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-3">
                {group.label}
              </p>
              {group.items.map((item) => (
                <NavRow key={item.to} item={item} badges={{ approvals_pending: pending }} />
              ))}
            </div>
          ))}
          <NavRow item={NAV_BOTTOM} badges={{}} className="mt-auto" />

          <div className="space-y-2 px-1 pb-1">
            <RunnerNotice />
            <a
              href="/api/docs" target="_blank" rel="noreferrer"
              className="flex items-center gap-2 px-1.5 py-1 text-[11px] text-ink-3 transition hover:text-ink-2"
            >
              <Activity size={12} /> API reference
            </a>
            <BuildStamp />
            <SessionControl />
          </div>
        </nav>

        {/* ---- left canvas: the QA grid -------------------------------- */}
        <main className="min-w-0 flex-1 overflow-y-auto">
          {pageMeta && (
            <div className="flex items-center gap-2.5 border-b border-line px-5 py-3.5">
              <pageMeta.icon size={17} className="text-accent" />
              <h1 className="text-[15px] font-semibold tracking-tight text-ink">{pageMeta.label}</h1>
            </div>
          )}
          <Suspense fallback={<div className="flex h-full items-center justify-center" aria-live="polite"><Spinner /></div>}>
          <Routes>
            <Route path="/" element={<Workspace />} />
            <Route path="/command" element={<Command />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/:runId" element={<RunDetail />} />
            <Route path="/releases" element={<Releases />} />
            <Route path="/tests" element={<Tests />} />
            <Route path="/requirements" element={<Requirements />} />
            <Route path="/author" element={<Author />} />
            <Route path="/approvals" element={<Approvals />} />
            <Route path="/intelligence" element={<Intelligence />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </Suspense>
        </main>

        {/* ---- right dock: QE Agent ------------------------------------ */}
        {/* Persistent rather than routed: the agent is how work is commanded,
            so it has to stay put while the canvas beneath it changes. Sized as a
            share of the viewport with a floor and a ceiling; a fixed pixel
            width is either cramped on a laptop or absurd on an ultrawide.
            Shown side-by-side only at `lg`+; narrower screens get the drawer. */}
        <aside className="hidden w-[32%] min-w-[340px] max-w-[520px] shrink-0 border-l border-line lg:block">
          <AgentCopilot />
        </aside>
      </div>

      {/* Below `lg`, the agent is a slide-in drawer reached from a floating
          button, so the command surface is never lost on a small screen. */}
      <button
        onClick={() => setChatOpen(true)}
        aria-label="Open QE Agent"
        className="fixed bottom-4 right-4 z-40 flex items-center gap-1.5 rounded-lg border border-line bg-surface-2 px-3.5 py-2.5 text-[12px] font-medium text-ink shadow-lg transition hover:border-line-strong hover:bg-surface-3 lg:hidden"
      >
        <MessagesSquare size={15} className="text-accent" />
        QE Agent
      </button>

      {chatOpen && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="QE Agent">
          <div
            className="absolute inset-0 bg-black/50 backdrop-blur-[1px]"
            onClick={() => setChatOpen(false)}
            aria-hidden="true"
          />
          <aside className="absolute right-0 top-0 flex h-full w-[min(92vw,420px)] flex-col border-l border-line bg-canvas shadow-2xl">
            <button
              onClick={() => setChatOpen(false)}
              aria-label="Close QE Agent"
              className="absolute right-2 top-2.5 z-10 flex h-7 w-7 items-center justify-center rounded-lg text-ink-3 transition hover:bg-surface-2 hover:text-ink"
            >
              <X size={16} />
            </button>
            <AgentCopilot />
          </aside>
        </div>
      )}
    </div>
    </WorkspaceProvider>
  );
}

function ModeBadge() {
  const [mode, setMode] = useState<string>('');

  useEffect(() => {
    let alive = true;
    // /api/health is the authority on the *live* mode; capabilities only lists
    // what is possible. Reading health avoids a stale badge after the model is
    // reconfigured from another tab.
    fetch('/api/health')
      .then((r) => r.json())
      .then((body) => { if (alive) setMode(body?.ai?.mode ?? 'no_ai'); })
      .catch(() => { if (alive) setMode('no_ai'); });
    return () => { alive = false; };
  }, []);

  if (!mode) return null;
  const noAI = mode === 'no_ai';
  return (
    <span
      title={
        noAI
          ? 'No model connected. The agent runs the mechanical layer deterministically (no tokens, no cloud). Connect any model in Settings to unlock exploring, planning and reasoning.'
          : `Model connected (${mode})`
      }
      className={clsx(
        'rounded-lg border px-2 py-1 text-[11px]',
        noAI ? 'border-line bg-surface-2 text-ink-3' : 'border-accent/30 bg-accent/10 text-accent',
      )}
    >
      {noAI ? 'Connect a model' : mode.replace('_', ' ')}
    </span>
  );
}

function NavRow({ item, badges, className }: {
  item: { to: string; label: string; icon: typeof FileText; end?: boolean; badgeKey?: 'approvals_pending' };
  badges: Partial<Record<'approvals_pending', number>>;
  className?: string;
}) {
  const Icon = item.icon;
  const badgeValue = item.badgeKey ? badges[item.badgeKey] : undefined;
  return (
    <NavLink
      to={item.to} end={item.end}
      className={({ isActive }) => clsx(
        'group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] transition-colors',
        isActive
          ? 'bg-surface-2 font-medium text-ink'
          : 'text-ink-3 hover:bg-surface-2/60 hover:text-ink-2',
        className,
      )}
    >
      {({ isActive }) => (
        <>
          <Icon size={15} className={isActive ? 'text-accent' : ''} />
          <span className="flex-1">{item.label}</span>
          {!!badgeValue && (
            <span className="rounded-sm bg-accent px-1.5 py-px text-[10px] font-semibold text-canvas">
              {badgeValue}
            </span>
          )}
        </>
      )}
    </NavLink>
  );
}

function RunnerNotice() {
  const { capabilities } = useApp();
  if (!capabilities || capabilities.execution.runner_installed) return null;
  return (
    <div className="rounded-lg border border-flaky/30 bg-flaky/10 p-2">
      <p className="text-[11px] font-medium text-flaky">Runner not installed</p>
      <p className="mt-1 text-[10px] leading-relaxed text-ink-3">{capabilities.execution.hint}</p>
    </div>
  );
}

/** Format an ISO instant as a short local date-time, or '-' if absent/unparseable. */
function shortLocal(iso: string | null | undefined): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '-';
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

/**
 * The build stamp: provenance of the code actually running, in the footer.
 *
 * A stale, reload-less server is invisible until you go looking; putting the
 * SHA and UI build time on screen makes drift a glance instead of an
 * investigation. A `-dirty` SHA (uncommitted changes are being served) is
 * flagged amber so it never reads as a clean, reproducible build.
 */
function BuildStamp() {
  const { capabilities } = useApp();
  const build = capabilities?.build;
  if (!build) return null;
  const dirty = build.sha.endsWith('-dirty');
  return (
    <p
      title={`version ${build.version} · commit ${build.sha}\nUI built ${shortLocal(build.built_at)}\nserver started ${shortLocal(build.started_at)}`}
      className="mono px-1.5 text-[9.5px] leading-relaxed text-ink-3"
    >
      v{build.version} · <span className={clsx(dirty && 'text-flaky')}>{build.sha}</span>
      <br />built {shortLocal(build.built_at)}
    </p>
  );
}
