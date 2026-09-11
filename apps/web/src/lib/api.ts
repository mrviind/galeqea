/**
 * Typed API client.
 *
 * Errors carry the server's own message rather than a generic "request failed":
 * this product's failure modes are things a user can usually act on ("the
 * runner is not installed", "that heal is no longer waiting"), and swallowing
 * that text into a toast that says "Error" throws away the only useful part.
 */

const BASE = '';

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
    this.name = 'ApiError';
  }
}

function readCookie(name: string): string {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : '';
}

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || 'GET').toUpperCase();
  // Double-submit CSRF: echo the readable csrf cookie back in a header on every
  // cookie-authenticated mutation. Bearer/API-token callers don't use cookies.
  const csrf = MUTATING.has(method) ? readCookie('galeqea_csrf') : '';
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    // Send the session + csrf cookies (same-origin in prod, cross-origin in dev).
    credentials: 'include',
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(csrf ? { 'x-csrf-token': csrf } : {}),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    let detail: unknown;
    try {
      const body = await res.json();
      detail = body;
      if (typeof body?.detail === 'string') message = body.detail;
      else if (Array.isArray(body?.detail)) message = body.detail.map((d: any) => d.msg).join('; ');
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, message, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const api = {
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: 'PATCH', body: JSON.stringify(body) }),
  del: <T>(p: string) => request<T>(p, { method: 'DELETE' }),
  upload: <T>(p: string, form: FormData) => request<T>(p, { method: 'POST', body: form }),
};

// --- authentication (multi-user deployments) ------------------------------
export interface AuthUser { id: string; email: string; name: string; role: string; }
export interface AuthConfig { password_login: boolean; oidc_enabled: boolean; single_user_mode: boolean; }

export const auth = {
  config: () => api.get<AuthConfig>('/api/auth/config'),
  me: () => api.get<AuthUser>('/api/auth/me'),
  login: (email: string, password: string) =>
    api.post<{ user: AuthUser; csrf_token: string }>('/api/auth/login', { email, password }),
  logout: () => api.post<{ ok: boolean }>('/api/auth/logout'),
};

// --- scoped API tokens (Settings) -----------------------------------------
export interface ApiToken {
  id: string; name: string; prefix: string; scopes: string[];
  expires_at: string | null; revoked: boolean; last_used_at: string | null; created_at: string | null;
}
export const tokens = {
  list: () => api.get<ApiToken[]>('/api/tokens'),
  create: (name: string, scopes: string[], ttl_days?: number | null) =>
    api.post<ApiToken & { token: string }>('/api/tokens', { name, scopes, ttl_days: ttl_days ?? null }),
  revoke: (id: string) => api.del<{ ok: boolean }>(`/api/tokens/${id}`),
};

// --- release management (WO#5) ---------------------------------------------
export interface Milestone {
  id: string; type?: string; name: string; version: string; status: string;
  target_date: string | null; exit_criteria: { metric: string; op: string; value: number }[];
  signoff: null | { by: string; decision: string; note?: string };
}
export interface Environment { id: string; name: string; base_url: string; build_label: string; tags: string[]; }
export interface ReleaseMetrics {
  counts: Record<string, number>; execution_progress: number; pass_rate: number;
  requirement_coverage: number; tested_coverage: number; p1_requirement_coverage: number;
  automation_ratio: number; flaky_rate: number; open_blockers: number; defect_density: number;
  mttr_ms: number; effort_variance: number;
}
export interface Readiness { verdict: string; criteria: { metric: string; op: string; target: any; actual: any; met: boolean }[]; }

export const releases = {
  list: (pid: string) => api.get<{ milestones: Milestone[] }>(`/api/projects/${pid}/milestones`),
  get: (pid: string, id: string) => api.get<Milestone>(`/api/projects/${pid}/milestones/${id}`),
  create: (pid: string, body: Partial<Milestone>) => api.post<Milestone>(`/api/projects/${pid}/milestones`, body),
  metrics: (pid: string, id: string) => api.get<ReleaseMetrics>(`/api/projects/${pid}/milestones/${id}/metrics.json`),
  readiness: (pid: string, id: string) => api.get<Readiness>(`/api/projects/${pid}/milestones/${id}/readiness`),
  signoff: (pid: string, id: string, decision: string, note?: string) =>
    api.post<Milestone>(`/api/projects/${pid}/milestones/${id}/signoff`, { decision, note }),
  cycles: (pid: string, milestoneId?: string) =>
    api.get<{ cycles: any[] }>(`/api/projects/${pid}/cycles${milestoneId ? `?milestone_id=${milestoneId}` : ''}`),
  environments: (pid: string) => api.get<{ environments: Environment[] }>(`/api/projects/${pid}/environments`),
  addEnvironment: (pid: string, body: { name: string; base_url: string; tags?: string[] }) =>
    api.post<Environment>(`/api/projects/${pid}/environments`, body),
};

export const defects = {
  list: (pid: string) =>
    api.get<{ defects: any[] }>(`/api/projects/${pid}/defects`),
  forResult: (pid: string, resultId: string) =>
    api.get<{ links: any[] }>(`/api/projects/${pid}/results/${resultId}/defects`),
  propose: (pid: string, resultId: string, provider?: string) =>
    api.post<{ status: string; approval_id: string; provider: string }>(
      `/api/projects/${pid}/results/${resultId}/defect`, provider ? { provider } : {}),
  refresh: (pid: string, mapId: string) =>
    api.post<any>(`/api/projects/${pid}/defects/${mapId}/refresh`, {}),
};

// --- domain types ---------------------------------------------------------
export type RunStatus =
  | 'queued' | 'running' | 'passed' | 'failed' | 'error'
  | 'cancelled' | 'skipped' | 'blocked' | 'flaky' | 'needs_review';

export interface Project {
  id: string; key: string; name: string; description: string;
  environments: Record<string, string>; default_environment: string;
  settings: Record<string, unknown>; archived: boolean;
}

export interface TestStep {
  index: number; action: string; intent: string; expected: string;
  target: Record<string, any>; value: Record<string, any>;
  options: Record<string, any>; element_id: string | null;
}

export interface TestCase {
  id: string; key: string; title: string; description: string;
  category: 'manual' | 'exploratory' | 'automated';
  status: 'proposed' | 'approved' | 'rejected' | 'draft' | 'archived';
  priority: string; risk: string; tags: string[]; rationale: string;
  preconditions: string[]; charter: string; requirement_refs: string[];
  covers?: string[]; technique?: string; assumptions?: string[];
  provenance: Record<string, any>; version: number; approved_by: string | null;
  flake_score: number; quarantined: boolean; steps: TestStep[];
}

export interface CoveredRule {
  rule_id: string; rule_type: string; technique: string; text: string;
  requirement_ref: string; source_anchor: Record<string, any>;
}

export interface RunSummary {
  id: string; number: number; title: string; status: RunStatus;
  trigger: string; environment: string; totals: Record<string, number>;
  duration_ms: number; headline: string; created_at: string; finished_at: string | null;
  queue_position?: number | null;
}

export interface RunResult {
  id: string; test_case_id: string; key: string; title: string; status: RunStatus;
  browser: string; duration_ms: number; error_message: string; error_type: string;
  classification: string; healed: boolean; signature: string;
  console_errors: any[]; network_failures: any[];
}

export interface RunDetail {
  run: RunSummary & {
    command: string; base_url: string; browsers: string[];
    triage: Record<string, any>; error: string; git_sha: string; git_branch: string;
    started_at: string | null;
  };
  results: RunResult[];
  artifacts: { id: string; kind: string; label: string; run_test_id: string | null; size_bytes: number }[];
  by_test_type?: { type: string; label: string; total: number; passed: number; failed: number; skipped: number }[];
  model_usage?: { calls: number; tokens: number; cost_usd: number; cache_hits: number; heals: number };
}

export interface ChatBlock { type: string; [k: string]: any }

export interface ChatMessage {
  id: string; role: 'user' | 'assistant' | 'system' | 'event';
  agent_role?: string; content: string; blocks: ChatBlock[];
  tool_calls: any[]; usage: Record<string, number>; error?: string; at: string;
  /** Surfaced by the orchestrator, e.g. text in the message that tried to
   *  override the agent's instructions. Rendered above the blocks so it is read
   *  before anything in the reply is acted on. */
  warnings?: { kind: string; severity?: string; message: string }[];
  /** One-click next steps derived from what the agent just did. */
  suggestions?: { label: string; text: string }[];
}

export interface Approval {
  id: string; action: string; title: string; summary: string; risk: string;
  required_role: string; status: string; payload: any; diff: any; evidence: any;
  requested_by_kind: string; agent_role: string; created_at: string; expires_at: string | null;
}

export interface Coverage {
  total_requirements: number; covered_requirements: number; automated_requirements: number;
  coverage_pct: number; automation_pct: number; headline: string;
  uncovered: any[]; weak: any[]; by_risk: Record<string, any>; journeys: Record<string, any>;
}

export interface Overview {
  project: { id: string; key: string; name: string; environments: Record<string, string> };
  tests: { total: number; by_category: Record<string, number>; by_status: Record<string, number>; awaiting_review: number; quarantined: number };
  runs: { recent: RunSummary[]; pass_rate: number };
  approvals_pending: number;
  coverage: Coverage;
  flaky: { key: string; title: string; score: number }[];
}

export interface BuildInfo {
  version: string;
  sha: string;
  built_at: string | null;
  started_at: string;
}

export interface Capabilities {
  version: string;
  build?: BuildInfo;
  ai_modes: any[];
  tools: { name: string; category: string; description: string; read_only: boolean; requires_approval: boolean; risk: string; external: boolean }[];
  approval_actions: string[];
  execution: { runner_installed: boolean; node_present: boolean; hint: string };
  export_targets: string[];
}
