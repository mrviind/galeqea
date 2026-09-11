import { FormEvent, ReactNode, useEffect, useState } from 'react';
import { LogIn, LogOut, ShieldCheck } from 'lucide-react';
import { ApiError, auth, AuthConfig, AuthUser } from '../lib/api';
import { Button, Spinner } from './primitives';
import { GaleQEALogo } from './ui/GaleQEALogo';

type Status = 'loading' | 'authed' | 'anon';

/**
 * Gates the whole app behind authentication on a shared deployment. In single-user
 * mode `/api/auth/me` returns the local owner, so the login screen never shows.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('loading');

  const check = () =>
    auth.me().then(
      () => setStatus('authed'),
      () => setStatus('anon'),
    );

  useEffect(() => { check(); }, []);

  if (status === 'loading') {
    return (
      <div className="flex h-screen items-center justify-center bg-canvas">
        <Spinner className="text-ink-3" />
      </div>
    );
  }
  if (status === 'anon') return <Login onAuthed={() => setStatus('authed')} />;
  return <>{children}</>;
}

/** Sidebar footer: who's signed in, and a way out. Hidden in single-user mode. */
export function SessionControl() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [multiuser, setMultiuser] = useState(false);

  useEffect(() => {
    auth.config().then((c) => setMultiuser(!c.single_user_mode), () => {});
    auth.me().then(setUser, () => {});
  }, []);

  if (!multiuser || !user) return null;

  async function signOut() {
    try { await auth.logout(); } finally { window.location.reload(); }
  }

  return (
    <div className="flex items-center justify-between gap-2 border-t border-line px-1.5 pt-2 text-[11px]">
      <span className="min-w-0 truncate text-ink-3" title={`${user.email} · ${user.role}`}>
        {user.email}
      </span>
      <button
        onClick={signOut}
        className="flex shrink-0 items-center gap-1 text-ink-3 transition hover:text-ink"
        title="Sign out"
      >
        <LogOut size={12} /> Sign out
      </button>
    </div>
  );
}

function Login({ onAuthed }: { onAuthed: () => void }) {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => { auth.config().then(setConfig, () => setConfig(null)); }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await auth.login(email.trim(), password);
      onAuthed();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not sign in. Try again.');
      setBusy(false);
    }
  }

  const field =
    'h-10 w-full rounded-lg border border-line bg-surface-2 px-3 text-[13px] text-ink ' +
    'placeholder:text-ink-3 outline-none transition focus:border-accent focus:bg-surface-3';

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <GaleQEALogo size="lg" />
          <p className="text-[13px] text-ink-2">Sign in to your test workspace</p>
        </div>

        <form
          onSubmit={submit}
          className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-6 shadow-2xl"
        >
          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium uppercase tracking-wide text-ink-3">Email</span>
            <input
              className={field} type="email" autoComplete="username" autoFocus
              value={email} onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com" required
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium uppercase tracking-wide text-ink-3">Password</span>
            <input
              className={field} type="password" autoComplete="current-password"
              value={password} onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••" required
            />
          </label>

          {error && (
            <p className="rounded-lg border border-fail/30 bg-fail/10 px-3 py-2 text-[12px] text-fail">
              {error}
            </p>
          )}

          <Button type="submit" variant="primary" disabled={busy} className="mt-1 w-full">
            {busy ? <Spinner className="text-canvas" /> : <LogIn size={15} />}
            {busy ? 'Signing in…' : 'Sign in'}
          </Button>

          {config?.oidc_enabled && (
            <>
              <div className="my-1 flex items-center gap-3 text-[11px] text-ink-3">
                <span className="h-px flex-1 bg-line" /> or <span className="h-px flex-1 bg-line" />
              </div>
              <a href="/api/auth/oidc/login" className="w-full">
                <Button variant="ghost" className="w-full">
                  <ShieldCheck size={15} /> Sign in with SSO
                </Button>
              </a>
            </>
          )}
        </form>

        <p className="mt-4 text-center text-[11px] text-ink-3">
          GaleQEA: AI-first test automation
        </p>
      </div>
    </div>
  );
}
