import { useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';

/**
 * Detects that the server is serving a newer UI build than the one running in
 * this tab, the exact hazard of rebuilding the UI under a running server.
 *
 * It reads `/api/health`'s `built_at` on mount, then again every 60s and after a
 * failed chunk load; when it changes, a non-blocking toast offers a reload. The
 * page keeps working meanwhile: an unknown chat block already renders a
 * "reload to view this update" fallback rather than a wrong card.
 */
export function VersionWatcher() {
  const [stale, setStale] = useState(false);
  const initial = useRef<string | null>(null);

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const health = await fetch('/api/health').then((r) => r.json());
        const built = health?.built_at ?? null;
        if (!alive || !built) return;
        if (initial.current === null) initial.current = built;
        else if (built !== initial.current) setStale(true);
      } catch {
        /* offline or restarting; try again next tick */
      }
    };
    void check();
    const id = window.setInterval(check, 60_000);
    // A failed dynamic import after a rebuild is a strong signal the bundle moved.
    const onError = (e: ErrorEvent) => {
      if (/loading chunk|dynamically imported|failed to fetch/i.test(String(e.message))) void check();
    };
    window.addEventListener('error', onError);
    return () => { alive = false; window.clearInterval(id); window.removeEventListener('error', onError); };
  }, []);

  if (!stale) return null;
  return (
    <div className="fixed bottom-4 left-1/2 z-[70] flex -translate-x-1/2 items-center gap-2 rounded-lg border border-line bg-surface-2 px-3 py-2 text-[12px] text-ink shadow-lg">
      <RefreshCw size={13} className="text-accent" />
      A new version of GaleQEA is available.
      <button
        onClick={() => window.location.reload()}
        className="rounded-md bg-accent px-2 py-0.5 text-[11px] font-semibold text-canvas transition hover:opacity-90"
      >
        Reload
      </button>
    </div>
  );
}
