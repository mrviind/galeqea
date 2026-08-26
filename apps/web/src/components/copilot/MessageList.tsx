import { useEffect, useLayoutEffect, useRef } from 'react';
import clsx from 'clsx';
import { ArrowRight, Check, Loader2, ShieldAlert, TriangleAlert, X, Zap } from 'lucide-react';
import type { ChatMessage } from '../../lib/api';
import { clock } from '../../lib/format';
import { Blocks } from '../Blocks';
import { GaleQEALogo } from '../ui/GaleQEALogo';

/**
 * The Copilot transcript.
 *
 * Two things it does that a plain chat log does not, both of which exist because
 * this agent takes actions rather than only answering:
 *
 *  - **Live status.** The orchestrator publishes timestamped progress over the
 *    event bus while it routes, calls tools and waits on approvals. Showing that
 *    is the difference between "it's working" and "it's hung".
 *  - **Blocks.** A reply can carry structured cards — an approval request, a run
 *    summary, a coverage table. They render as themselves, not as pasted JSON.
 */
/** One tool invocation, from the moment it starts to the moment it resolves. */
export interface ToolActivity {
  key: string;
  tool: string;
  summary: string;
  state: 'running' | 'done' | 'failed';
  readOnly: boolean;
  requiresApproval: boolean;
  durationMs?: number;
}

export function MessageList({
  messages, draft, tools, status, busy, suggestions, onSuggest,
}: {
  messages: ChatMessage[];
  /** The assistant's reply while it is still streaming in. */
  draft: string;
  tools: ToolActivity[];
  status: { label: string; at: string }[];
  busy: boolean;
  suggestions: string[];
  onSuggest: (text: string) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  // Follow the transcript, but stop the moment the reader scrolls up. Yanking
  // someone back to the bottom mid-read is the rudest thing a live log can do.
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const onScroll = () => {
      pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  useLayoutEffect(() => {
    if (pinned.current && scroller.current) {
      scroller.current.scrollTop = scroller.current.scrollHeight;
    }
  }, [messages, draft, status, tools, busy]);

  return (
    <div ref={scroller} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-3 py-3.5">
      {messages.length === 0 && !busy && (
        <div className="space-y-3 pt-2">
          <div className="space-y-1.5">
            <GaleQEALogo size="lg" showText={false} />
            <h2 className="pt-1.5 text-[14px] font-semibold tracking-tight text-ink">
              Drive quality from here.
            </h2>
            <p className="text-[11.5px] leading-relaxed text-ink-3">
              Most commands resolve with no model at all — instantly, offline, and
              predictably. Anything that changes state is queued for your approval first.
            </p>
          </div>
          <div className="space-y-1">
            {suggestions.map((text) => (
              <button
                key={text}
                onClick={() => onSuggest(text)}
                className="flex w-full items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 py-1.5 text-left text-[11.5px] text-ink-2 transition hover:border-line-strong hover:text-ink"
              >
                <span className="text-ink-3">›</span>
                <span className="min-w-0 flex-1 truncate">{text}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {messages.map((message, index) => (
        <Message
          key={message.id}
          message={message}
          nextSteps={index === messages.length - 1 && !busy ? message.suggestions : undefined}
          onSuggest={onSuggest}
        />
      ))}

      {draft && (
        <article className="space-y-1.5" aria-live="polite">
          <header className="flex items-baseline gap-2">
            <span className="text-[10.5px] font-semibold uppercase tracking-wide text-accent">Copilot</span>
            <span className="text-[10px] text-ink-3">streaming</span>
          </header>
          <div className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-ink">
            {draft}
            <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-accent align-middle" aria-hidden="true" />
          </div>
        </article>
      )}

      {tools.length > 0 && (
        <ul className="space-y-1">
          {tools.map((tool) => <ToolRow key={tool.key} activity={tool} />)}
        </ul>
      )}

      {status.length > 0 && (
        <ol className="space-y-1 rounded-lg border border-line bg-surface-2/60 px-2.5 py-2">
          {status.map((step, index) => (
            <li key={`${step.at}-${index}`} className="flex items-baseline gap-2 text-[11px]">
              <span className="mono shrink-0 text-ink-3">{clock(step.at)}</span>
              <span className={clsx('min-w-0 flex-1', index === status.length - 1 ? 'text-ink-2' : 'text-ink-3')}>
                {step.label}
              </span>
            </li>
          ))}
        </ol>
      )}

      {busy && !draft && status.length === 0 && tools.length === 0 && (
        <p className="flex items-center gap-2 px-0.5 text-[11.5px] text-ink-3">
          <Loader2 size={12} className="animate-spin" /> Working…
        </p>
      )}
    </div>
  );
}

function Message({ message, nextSteps, onSuggest }: {
  message: ChatMessage;
  nextSteps?: ChatMessage['suggestions'];
  onSuggest: (text: string) => void;
}) {
  const isUser = message.role === 'user';
  return (
    <article className="space-y-1.5">
      <header className="flex items-baseline gap-2">
        <span className={clsx('text-[10.5px] font-semibold uppercase tracking-wide',
          isUser ? 'text-ink-3' : 'text-accent')}>
          {isUser ? 'You' : 'Copilot'}
        </span>
        {message.at && <span className="mono text-[10px] text-ink-3">{clock(message.at)}</span>}
      </header>

      <div
        className={clsx(
          'whitespace-pre-wrap text-[12.5px] leading-relaxed',
          isUser
            ? 'rounded-lg border border-line bg-surface-2 px-2.5 py-2 text-ink-2'
            : 'text-ink',
        )}
      >
        {message.content}
      </div>

      {/* Warnings are rendered above the blocks: if a message was flagged for
          prompt injection, that has to be read before its contents are acted on. */}
      {(message.warnings ?? []).map((warning: { kind: string; message: string }, index: number) => (
        <p key={index} className="flex items-start gap-1.5 rounded-lg border border-flaky/30 bg-flaky/10 px-2.5 py-1.5 text-[11px] leading-relaxed text-flaky">
          {warning.kind === 'prompt_injection'
            ? <ShieldAlert size={12} className="mt-px shrink-0" />
            : <TriangleAlert size={12} className="mt-px shrink-0" />}
          {warning.message}
        </p>
      ))}

      {!!message.blocks?.length && <Blocks blocks={message.blocks} />}

      {/* Next steps: the QE pipeline as one-click prompts. Derived from the
          last tool that ran, never from the model, so they are consistent and
          free. Sent verbatim, so the user can read exactly what will happen. */}
      {!!nextSteps?.length && (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {nextSteps.map((step) => (
            <button
              key={step.text}
              onClick={() => onSuggest(step.text)}
              title={step.text}
              className="flex items-center gap-1 rounded-lg border border-line bg-surface-2 px-2 py-1 text-[11px] text-ink-2 transition hover:border-accent/40 hover:text-ink"
            >
              <ArrowRight size={10} className="text-ink-3" />
              {step.label}
            </button>
          ))}
        </div>
      )}
    </article>
  );
}


/**
 * A single tool execution, live.
 *
 * The row states three things the user actually needs while waiting: which tool
 * is running, whether it only reads (so nothing is changing under them), and
 * whether it is going to need their approval — which is the difference between
 * "wait a moment" and "you are about to be asked something".
 *
 * Cyan is GaleQEA's `review` token rather than a raw palette value, so tool
 * activity stays distinct from the run statuses (green pass, red fail, amber
 * flaky) that already own the rest of the colour vocabulary. The bolt is a
 * Lucide glyph rather than an emoji: it stays crisp at 11px and matches the
 * icon set used everywhere else.
 */
function ToolRow({ activity }: { activity: ToolActivity }) {
  const { state, tool, summary, readOnly, requiresApproval, durationMs } = activity;
  return (
    <li
      className={clsx(
        'flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-[11.5px] transition-colors',
        state === 'running' && 'border-review/30 bg-review/[0.07] text-review',
        state === 'done' && 'border-line bg-surface-2/60 text-ink-3',
        state === 'failed' && 'border-fail/30 bg-fail/[0.07] text-fail',
      )}
    >
      <span className="shrink-0">
        {state === 'running' && <Zap size={11} className="animate-pulse" />}
        {state === 'done' && <Check size={11} />}
        {state === 'failed' && <X size={11} />}
      </span>

      {/* The tool name is always shown, in every state. A finished row that
          reads only "0 result(s)" is unreadable: the summary is meaningless
          without knowing which tool produced it, and these tools resolve in
          single-digit milliseconds, so the running state is often a flash. */}
      <span className="mono shrink-0">{tool}</span>
      <span className="min-w-0 flex-1 truncate">
        {state === 'running' ? <span className="opacity-80">executing…</span> : summary}
      </span>

      {state === 'running' && requiresApproval && (
        <span className="shrink-0 rounded-md border border-flaky/30 bg-flaky/10 px-1.5 py-px text-[9.5px] font-medium text-flaky">
          needs approval
        </span>
      )}
      {state === 'running' && readOnly && !requiresApproval && (
        <span className="shrink-0 text-[9.5px] text-review/70">read-only</span>
      )}
      {durationMs != null && (
        <span className="shrink-0 tabular-nums text-[9.5px] text-ink-3">{durationMs}ms</span>
      )}
    </li>
  );
}
