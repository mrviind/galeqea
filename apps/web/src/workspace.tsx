import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

/**
 * Workspace state — what the Right Dock puts on the Left Canvas.
 *
 * React Context rather than a new store library: `state.tsx` already holds the
 * app's global state this way, and adding a second state paradigm for three
 * fields would mean every future contributor has to learn which of two patterns
 * applies where.
 *
 * The store is deliberately thin. It holds what the agent produced and what the
 * mock runner reported; it does not fetch, transform or decide. Panes render it.
 */

export interface RequirementsPane {
  title: string;
  markdown: string;
  count: number;
  at: string;
}

export interface ScriptFile {
  filename: string;
  language: string;
  code: string;
}

export interface TestScriptPane {
  title: string;
  files: ScriptFile[];
  unresolved: string[];
  requirementRef: string;
  at: string;
}

export type TelemetryStatus = 'idle' | 'running' | 'passed' | 'failed';

export interface TelemetryLine {
  /** `pass` and `fail` are drawn from the status palette; the rest are neutral. */
  level: 'info' | 'pass' | 'fail' | 'warn';
  text: string;
  at: string;
}

export interface TelemetryPane {
  status: TelemetryStatus;
  logs: TelemetryLine[];
  startedAt: string | null;
  durationMs: number | null;
  target: string;
}

export interface ReviewFinding {
  severity: 'critical' | 'high' | 'medium' | 'low';
  step: number | null;
  kind: string;
  message: string;
}

export interface ReviewPane {
  title: string;
  verdict: 'sound' | 'advisory' | 'needs_work' | 'blocked';
  findings: ReviewFinding[];
  at: string;
}

export interface PlanStep {
  index: number;
  tool: string;
  why: string;
  arguments_summary?: string;
  effect: 'read-only' | 'writes' | 'needs approval' | 'unknown';
  known_tool: boolean;
}

export interface PlanPane {
  goal: string;
  steps: PlanStep[];
  writesState: boolean;
  at: string;
}

interface WorkspaceState {
  activeRequirements: RequirementsPane | null;
  activeTestScript: TestScriptPane | null;
  activeTelemetry: TelemetryPane;
  activeReview: ReviewPane | null;
  activePlan: PlanPane | null;

  setRequirements: (pane: RequirementsPane) => void;
  setTestScript: (pane: TestScriptPane) => void;
  clearPane: (pane: 'requirements' | 'test_matrix' | 'telemetry' | 'rca') => void;

  /** Called by the agent when a tool returns a `_ui` projection. */
  applyToolProjection: (projection: Record<string, unknown>) => 'requirements' | 'test_matrix' | 'rca' | null;

  beginRun: (target: string) => void;
  appendLog: (line: Omit<TelemetryLine, 'at'>) => void;
  finishRun: (status: Exclude<TelemetryStatus, 'idle' | 'running'>, durationMs: number) => void;
}

const IDLE_TELEMETRY: TelemetryPane = {
  status: 'idle', logs: [], startedAt: null, durationMs: null, target: '',
};

const Ctx = createContext<WorkspaceState | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [activeRequirements, setActiveRequirements] = useState<RequirementsPane | null>(null);
  const [activeTestScript, setActiveTestScript] = useState<TestScriptPane | null>(null);
  const [activeTelemetry, setActiveTelemetry] = useState<TelemetryPane>(IDLE_TELEMETRY);
  const [activeReview, setActiveReview] = useState<ReviewPane | null>(null);
  const [activePlan, setActivePlan] = useState<PlanPane | null>(null);

  const setRequirements = useCallback((pane: RequirementsPane) => setActiveRequirements(pane), []);
  const setTestScript = useCallback((pane: TestScriptPane) => setActiveTestScript(pane), []);

  const clearPane = useCallback((pane: 'requirements' | 'test_matrix' | 'telemetry' | 'rca') => {
    if (pane === 'requirements') setActiveRequirements(null);
    if (pane === 'test_matrix') { setActiveTestScript(null); setActivePlan(null); }
    if (pane === 'telemetry') setActiveTelemetry(IDLE_TELEMETRY);
    if (pane === 'rca') setActiveReview(null);
  }, []);

  /**
   * Translate a server-sent `_ui` projection into pane state.
   *
   * Validated rather than trusted: the payload arrives over a socket, and a
   * malformed or unknown projection must leave the canvas as it was instead of
   * blanking a pane the user was reading. Returns which pane it filled, so the
   * caller can focus it.
   */
  const applyToolProjection = useCallback((projection: Record<string, unknown>) => {
    const pane = projection.pane;
    const at = new Date().toISOString();

    if (pane === 'requirements' && typeof projection.markdown === 'string') {
      setActiveRequirements({
        title: typeof projection.title === 'string' ? projection.title : 'Requirements',
        markdown: projection.markdown,
        count: typeof projection.count === 'number' ? projection.count : 0,
        at,
      });
      return 'requirements' as const;
    }

    if (pane === 'rca' && projection.review && typeof projection.review === 'object') {
      const review = projection.review as { verdict: ReviewPane['verdict']; findings: ReviewFinding[] };
      setActiveReview({
        title: typeof projection.title === 'string' ? projection.title : 'Review',
        verdict: review.verdict,
        findings: Array.isArray(review.findings) ? review.findings : [],
        at,
      });
      return 'rca' as const;
    }

    if (pane === 'test_matrix' && projection.plan && typeof projection.plan === 'object') {
      const plan = projection.plan as { goal: string; steps: PlanStep[]; writes_state: boolean };
      setActivePlan({
        goal: plan.goal,
        steps: Array.isArray(plan.steps) ? plan.steps : [],
        writesState: !!plan.writes_state,
        at,
      });
      return 'test_matrix' as const;
    }

    if (pane === 'test_matrix' && Array.isArray(projection.files)) {
      const files = (projection.files as unknown[]).filter(
        (f): f is ScriptFile =>
          !!f && typeof (f as ScriptFile).filename === 'string' && typeof (f as ScriptFile).code === 'string',
      );
      if (!files.length) return null;
      setActivePlan(null);
      setActiveTestScript({
        title: typeof projection.title === 'string' ? projection.title : 'Generated spec',
        files,
        unresolved: Array.isArray(projection.unresolved) ? (projection.unresolved as string[]) : [],
        requirementRef: typeof projection.requirement_ref === 'string' ? projection.requirement_ref : '',
        at,
      });
      return 'test_matrix' as const;
    }

    return null;
  }, []);

  const beginRun = useCallback((target: string) => {
    setActiveTelemetry({
      status: 'running', logs: [], startedAt: new Date().toISOString(), durationMs: null, target,
    });
  }, []);

  const appendLog = useCallback((line: Omit<TelemetryLine, 'at'>) => {
    setActiveTelemetry((prev) => ({
      ...prev,
      logs: [...prev.logs, { ...line, at: new Date().toISOString() }],
    }));
  }, []);

  const finishRun = useCallback((status: 'passed' | 'failed', durationMs: number) => {
    setActiveTelemetry((prev) => ({ ...prev, status, durationMs }));
  }, []);

  const value = useMemo<WorkspaceState>(() => ({
    activeRequirements, activeTestScript, activeTelemetry, activeReview, activePlan,
    setRequirements, setTestScript, clearPane, applyToolProjection,
    beginRun, appendLog, finishRun,
  }), [activeRequirements, activeTestScript, activeTelemetry, activeReview, activePlan,
       setRequirements, setTestScript, clearPane, applyToolProjection,
       beginRun, appendLog, finishRun]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/**
 * Ask the canvas to show a pane.
 *
 * A DOM event rather than another context field: the agent and the tab strip
 * are siblings with no shared ancestor below <App>, and threading a setter
 * through both would couple every intermediate component to a concern neither
 * of them owns.
 */
export const FOCUS_PANE_EVENT = 'galeqea:focus-pane';

export function focusPane(pane: 'requirements' | 'test_matrix' | 'telemetry' | 'rca'): void {
  window.dispatchEvent(new CustomEvent(FOCUS_PANE_EVENT, { detail: pane }));
}

export function useWorkspace(): WorkspaceState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useWorkspace must be used inside <WorkspaceProvider>');
  return ctx;
}
