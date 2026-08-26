import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { api } from './lib/api';
import type { Capabilities, Overview, Project } from './lib/api';
import { EventStream } from './lib/stream';
import type { StreamEvent } from './lib/stream';

interface AppState {
  projects: Project[];
  project: Project | null;
  selectProject: (id: string) => void;
  overview: Overview | null;
  refreshOverview: () => Promise<void>;
  capabilities: Capabilities | null;
  events: StreamEvent[];
  subscribe: (fn: (e: StreamEvent) => void) => () => void;
  connected: boolean;
  loading: boolean;
  error: string | null;
}

const Ctx = createContext<AppState | null>(null);

const MAX_EVENTS = 600;

export function AppProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const listeners = useRef(new Set<(e: StreamEvent) => void>());
  const streamRef = useRef<EventStream | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [list, caps] = await Promise.all([
          api.get<Project[]>('/api/projects'),
          api.get<Capabilities>('/api/capabilities'),
        ]);
        setProjects(list);
        setCapabilities(caps);
        const stored = localStorage.getItem('galeqea.project');
        setProject(list.find((p) => p.id === stored) ?? list[0] ?? null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'could not reach the GaleQEA API');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const refreshOverview = useCallback(async () => {
    if (!project) return;
    try {
      setOverview(await api.get<Overview>(`/api/projects/${project.id}/overview`));
    } catch { /* transient; the next event or poll will retry */ }
  }, [project]);

  useEffect(() => { void refreshOverview(); }, [refreshOverview]);

  // One stream per project; re-established when the project changes.
  useEffect(() => {
    if (!project) return;
    const stream = new EventStream(project.id);
    streamRef.current = stream;

    const off = stream.subscribe((event) => {
      if (event.type === '_connection') {
        setConnected(event.payload.state === 'open');
        return;
      }
      setEvents((prev) => {
        const next = [...prev, event];
        return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next;
      });
      listeners.current.forEach((fn) => fn(event));

      // Terminal events change counts the dashboard shows, so refresh then -
      // rather than polling on a timer that is usually wasted work.
      if (['run.finished', 'approval.decided', 'chat.message'].includes(event.type)) {
        void refreshOverview();
      }
    });
    stream.connect();
    setEvents([]);

    return () => { off(); stream.close(); };
  }, [project, refreshOverview]);

  const selectProject = useCallback((id: string) => {
    const found = projects.find((p) => p.id === id);
    if (!found) return;
    localStorage.setItem('galeqea.project', id);
    setProject(found);
  }, [projects]);

  const subscribe = useCallback((fn: (e: StreamEvent) => void) => {
    listeners.current.add(fn);
    return () => { listeners.current.delete(fn); };
  }, []);

  const value = useMemo<AppState>(() => ({
    projects, project, selectProject, overview, refreshOverview,
    capabilities, events, subscribe, connected, loading, error,
  }), [projects, project, selectProject, overview, refreshOverview, capabilities, events, subscribe, connected, loading, error]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useApp must be used inside <AppProvider>');
  return ctx;
}

/** Subscribe to a filtered slice of the live stream. */
export function useEvents(types: string[], handler: (e: StreamEvent) => void) {
  const { subscribe } = useApp();
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => subscribe((event) => {
    if (types.length === 0 || types.includes(event.type)) ref.current(event);
  }), [subscribe, types.join('|')]); // eslint-disable-line react-hooks/exhaustive-deps
}
