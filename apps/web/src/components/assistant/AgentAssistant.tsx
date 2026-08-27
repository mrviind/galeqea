import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import type { ChatMessage } from '../../lib/api';
import { useApp, useEvents } from '../../state';
import { focusPane, useWorkspace } from '../../workspace';
import { MessageInput } from './MessageInput';
import { MessageList, type ToolActivity } from './MessageList';

/**
 * The Assistant — the persistent right dock.
 *
 * Every credential it uses is resolved server-side. There is no key in this
 * component, no key in the store, and nothing written to localStorage beyond the
 * user's model preference. The browser sends a model *identifier*; the server
 * unseals the matching credential from the vault, records the use in the audit
 * ledger, and returns the reply.
 */

/** Suggestions double as documentation of what plain English is understood. */
const SUGGESTIONS = [
  'run the smoke tests on staging',
  'rerun only failed',
  "what's not tested?",
  'which tests are flaky?',
  'why did the last run fail?',
  'schedule regression nightly at 2am',
];

const MODEL_PREF_KEY = 'galeqea.assistant.model';

export function AgentAssistant() {
  const { project } = useApp();
  const { applyToolProjection } = useWorkspace();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ label: string; at: string }[]>([]);
  const [tools, setTools] = useState<ToolActivity[]>([]);
  // The reply as it streams in. Held apart from `messages` on purpose: the
  // draft is ephemeral and unpersisted, and the moment the server returns the
  // real message the draft is discarded rather than reconciled.
  const [draft, setDraft] = useState('');
  const [preview, setPreview] = useState<{ intent: string; explanation: string; path: string } | null>(null);
  const [model, setModel] = useState<{ provider: string; model: string } | null>(() => {
    // A preference, not a secret — which model to talk to, never how to reach it.
    try {
      const raw = localStorage.getItem(MODEL_PREF_KEY);
      return raw ? (JSON.parse(raw) as { provider: string; model: string }) : null;
    } catch {
      return null;
    }
  });

  const previewTimer = useRef<number | null>(null);
  const lastTurn = useRef<number>(-1);

  useEffect(() => {
    if (model) localStorage.setItem(MODEL_PREF_KEY, JSON.stringify(model));
  }, [model]);

  // Start or resume this project's conversation.
  useEffect(() => {
    if (!project) return;
    let alive = true;
    (async () => {
      const sessions = await api.get<{ id: string }[]>(`/api/projects/${project.id}/chat/sessions`);
      const session = sessions[0] ?? (await api.post<{ id: string }>(`/api/projects/${project.id}/chat/sessions`, {}));
      if (!alive) return;
      setSessionId(session.id);
      const detail = await api.get<{ messages?: ChatMessage[] }>(
        `/api/projects/${project.id}/chat/sessions/${session.id}`,
      );
      if (alive) setMessages(detail.messages ?? []);
    })().catch(() => { /* the composer still renders; sending surfaces the error */ });
    return () => { alive = false; };
  }, [project]);

  // Tool execution is tracked separately from narrative status, because the two
  // answer different questions. Status says what the agent is thinking about;
  // tool activity says what it is *doing to your project* — which tool, whether
  // it only reads, and whether it will need your approval. Flattening both into
  // one list of grey lines throws that away.
  useEvents(['chat.status', 'chat.delta', 'agent.started', 'agent.tool_call', 'agent.step', 'agent.finished'], (event) => {
    if (event.type === 'agent.started') {
      setDraft('');
      return;
    }
    if (event.type === 'chat.delta') {
      // Deltas can arrive for a tool-calling turn *and* the final answer. A new
      // turn index means the earlier text was a preamble to a tool call, not
      // the answer, so the draft starts over rather than concatenating both.
      const { text, turn } = event.payload as { text: string; turn?: number };
      setDraft((prev) => (turn !== undefined && turn !== lastTurn.current ? text : prev + text));
      if (turn !== undefined) lastTurn.current = turn;
      return;
    }
    if (event.type === 'agent.finished') {
      setStatus([]);
      setTools([]);
      return;
    }

    if (event.type === 'agent.tool_call') {
      const { tool, summary, read_only, requires_approval, step } = event.payload;
      setTools((prev) => [...prev, {
        key: `${step}:${tool}`,
        tool,
        summary: summary ?? '',
        state: 'running',
        readOnly: read_only !== false,
        requiresApproval: !!requires_approval,
      }]);
      return;
    }

    if (event.type === 'agent.step') {
      const { tool, ok, duration_ms, summary, step, ui } = event.payload;

      // A tool that returned a `_ui` projection drives the Left Canvas. The
      // pane is focused only when the tool succeeded — pushing a failed lookup
      // onto the canvas would replace whatever the user was reading with an
      // error they did not ask to see.
      if (ok !== false && ui && typeof ui === 'object') {
        const filled = applyToolProjection(ui as Record<string, unknown>);
        if (filled) focusPane(filled);
      }

      // Resolve the matching running entry rather than appending a second row —
      // otherwise every tool appears twice, once starting and once finished.
      setTools((prev) => {
        const key = `${step}:${tool}`;
        const index = prev.findIndex((t) => t.key === key && t.state === 'running');
        if (index < 0) return prev;
        const next = [...prev];
        next[index] = {
          ...next[index]!,
          state: ok === false ? 'failed' : 'done',
          durationMs: duration_ms,
          summary: summary || next[index]!.summary,
        };
        return next;
      });
      return;
    }

    const label = event.payload.label;
    if (!label) return;
    setStatus((prev) => [...prev.slice(-5), { label, at: event.ts }]);
  });

  // Ask the server how it *would* route this, so the user can see whether they
  // are about to spend a token before they press send.
  const schedulePreview = useCallback((text: string) => {
    if (previewTimer.current) window.clearTimeout(previewTimer.current);
    if (!project || text.trim().length < 4) { setPreview(null); return; }
    previewTimer.current = window.setTimeout(async () => {
      try {
        setPreview(await api.post(`/api/projects/${project.id}/chat/preview-command`, { text }));
      } catch {
        setPreview(null);
      }
    }, 350);
  }, [project]);

  const send = useCallback(async (override?: string) => {
    const text = (override ?? input).trim();
    if (!text || !project || !sessionId || busy) return;

    setInput('');
    setPreview(null);
    setBusy(true);
    setMessages((prev) => [
      ...prev,
      { id: `local-${Date.now()}`, role: 'user', content: text, blocks: [], tool_calls: [], usage: {}, at: new Date().toISOString() },
    ]);

    try {
      const reply = await api.post<{
        message: ChatMessage;
        warnings?: ChatMessage['warnings'];
        suggestions?: ChatMessage['suggestions'];
      }>(
        `/api/projects/${project.id}/chat/sessions/${sessionId}/messages`,
        // The model identifier goes up; the credential never comes down.
        { text, provider: model?.provider, model: model?.model },
      );
      setDraft('');
      setMessages((prev) => [
        ...prev,
        { ...reply.message, warnings: reply.warnings, suggestions: reply.suggestions },
      ]);
    } catch (error) {
      // The failure becomes a message rather than a toast that vanishes: the
      // user needs to read "no Anthropic key in the vault" long enough to act.
      setMessages((prev) => [...prev, {
        id: `err-${Date.now()}`, role: 'assistant',
        content: error instanceof Error ? error.message : 'That request failed.',
        blocks: [], tool_calls: [], usage: {}, at: new Date().toISOString(),
        error: 'request_failed',
      }]);
    } finally {
      setBusy(false);
      setStatus([]);
      setTools([]);
      setDraft('');
    }
  }, [input, project, sessionId, busy, model]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface">
      <header className="flex h-10 shrink-0 items-center gap-2 border-b border-line px-3">
        <span className="text-[12px] font-semibold tracking-tight text-ink">Assistant</span>
        <span className="text-[10.5px] text-ink-3">plain English, live</span>
      </header>

      <MessageList
        messages={messages}
        draft={draft}
        tools={tools}
        status={status}
        busy={busy}
        suggestions={SUGGESTIONS}
        onSuggest={(text) => void send(text)}
      />

      <MessageInput
        value={input}
        onChange={(text) => { setInput(text); schedulePreview(text); }}
        onSend={() => void send()}
        busy={busy}
        disabled={!project}
        preview={preview}
        model={model}
        onModelChange={setModel}
      />
    </div>
  );
}
