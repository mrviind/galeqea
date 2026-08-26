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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
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
  provenance: Record<string, any>; version: number; approved_by: string | null;
  flake_score: number; quarantined: boolean; steps: TestStep[];
}

export interface RunSummary {
  id: string; number: number; title: string; status: RunStatus;
  trigger: string; environment: string; totals: Record<string, number>;
  duration_ms: number; headline: string; created_at: string; finished_at: string | null;
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
}

export interface ChatBlock { type: string; [k: string]: any }

export interface ChatMessage {
  id: string; role: 'user' | 'assistant' | 'system' | 'event';
  agent_role?: string; content: string; blocks: ChatBlock[];
  tool_calls: any[]; usage: Record<string, number>; error?: string; at: string;
  /** Surfaced by the orchestrator — e.g. text in the message that tried to
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

export interface Capabilities {
  version: string;
  ai_modes: any[];
  tools: { name: string; category: string; description: string; read_only: boolean; requires_approval: boolean; risk: string; external: boolean }[];
  approval_actions: string[];
  execution: { runner_installed: boolean; node_present: boolean; hint: string };
  export_targets: string[];
}
