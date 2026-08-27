import { useEffect, useState } from 'react';
import { NavLink, Navigate, Route, Routes } from 'react-router-dom';
import clsx from 'clsx';
import {
  Activity, AlertTriangle, BrainCircuit, FileText, Gauge, LayoutGrid, MousePointerClick, PlayCircle,
  Settings as SettingsIcon, ShieldCheck, TestTube2, Wifi, WifiOff,
} from 'lucide-react';
import { useApp } from './state';
import { AgentAssistant } from './components/assistant/AgentAssistant';
import { Spinner } from './components/primitives';
import { QEAgentLogo } from './components/ui/QEAgentLogo';
import Command from './pages/Command';
import Runs from './pages/Runs';
import RunDetail from './pages/RunDetail';
import Tests from './pages/Tests';
import Requirements from './pages/Requirements';
import Workspace from './pages/Workspace';
import { WorkspaceProvider } from './workspace';
import Author from './pages/Author';
import Approvals from './pages/Approvals';
import Intelligence from './pages/Intelligence';
import Settings from './pages/Settings';

const NAV = [
  { to: '/', label: 'Workspace', icon: LayoutGrid, end: true },
  { to: '/command', label: 'Command', icon: Gauge },
  { to: '/runs', label: 'Runs', icon: PlayCircle },
  { to: '/tests', label: 'Tests', icon: TestTube2 },
  { to: '/requirements', label: 'Requirements', icon: FileText },
  { to: '/author', label: 'Author', icon: MousePointerClick },
  { to: '/approvals', label: 'Approvals', icon: ShieldCheck },
  { to: '/intelligence', label: 'Intelligence', icon: BrainCircuit },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
];

export default function App() {
  const { project, projects, selectProject, overview, connected, loading, error, capabilities } = useApp();

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center gap-3 text-ink-3">
        <Spinner className="text-accent" />
        <span className="text-sm">Starting QE Agent…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="panel max-w-md space-y-3 p-6">
          <div className="flex items-center gap-2 text-fail">
            <AlertTriangle size={18} />
            <h1 className="text-sm font-semibold">Cannot reach the QE Agent API</h1>
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
    <div className="rounded-lg flex h-full flex-col bg-canvas">
      {/* ---- top bar --------------------------------------------------- */}
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line px-3">
        <div className="flex items-center pr-1">
          <QEAgentLogo size="sm" />
        </div>

        <div className="h-4 w-px bg-line" />

        <select
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
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* ---- left rail ----------------------------------------------- */}
        <nav className="flex w-[188px] shrink-0 flex-col gap-0.5 border-r border-line p-2">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to} to={to} end={end}
              className={({ isActive }) => clsx(
                'group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] transition-colors',
                isActive
                  ? 'bg-surface-2 font-medium text-ink'
                  : 'text-ink-3 hover:bg-surface-2/60 hover:text-ink-2',
              )}
            >
              {({ isActive }) => (
                <>
                  <Icon size={15} className={isActive ? 'text-accent' : ''} />
                  <span className="flex-1">{label}</span>
                  {label === 'Approvals' && pending > 0 && (
                    <span className="rounded-full bg-accent px-1.5 py-px text-[10px] font-semibold text-canvas">
                      {pending}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          ))}

          <div className="mt-auto space-y-2 px-1 pb-1">
            <RunnerNotice />
            <a
              href="/api/docs" target="_blank" rel="noreferrer"
              className="flex items-center gap-2 px-1.5 py-1 text-[11px] text-ink-3 transition hover:text-ink-2"
            >
              <Activity size={12} /> API reference
            </a>
          </div>
        </nav>

        {/* ---- left canvas: the QA grid -------------------------------- */}
        <main className="min-w-0 flex-1 overflow-y-auto">
          <Routes>
            <Route path="/" element={<Workspace />} />
            <Route path="/command" element={<Command />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/:runId" element={<RunDetail />} />
            <Route path="/tests" element={<Tests />} />
            <Route path="/requirements" element={<Requirements />} />
            <Route path="/author" element={<Author />} />
            <Route path="/approvals" element={<Approvals />} />
            <Route path="/intelligence" element={<Intelligence />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>

        {/* ---- right dock: the Assistant ---------------------------- */}
        {/* Persistent rather than routed: the agent is how work is commanded,
            so it has to stay put while the canvas beneath it changes. Sized as a
            share of the viewport with a floor and a ceiling — a fixed pixel
            width is either cramped on a laptop or absurd on an ultrawide. */}
        <aside className="hidden w-[32%] min-w-[340px] max-w-[520px] shrink-0 border-l border-line lg:block">
          <AgentAssistant />
        </aside>
      </div>
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
          ? 'No-AI mode: no model calls and no outbound network traffic. Every core feature still works.'
          : `AI enabled (${mode})`
      }
      className={clsx(
        'rounded-lg border px-2 py-1 text-[11px]',
        noAI ? 'border-line bg-surface-2 text-ink-3' : 'border-accent/30 bg-accent/10 text-accent',
      )}
    >
      {noAI ? 'No-AI mode' : `AI · ${mode.replace('_', ' ')}`}
    </span>
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
