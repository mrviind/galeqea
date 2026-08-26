/**
 * Live event stream.
 *
 * One WebSocket per project feeds every live surface. It reconnects with
 * exponential backoff and asks the server to replay recent history on
 * reconnect, so a dropped connection mid-run refills the timeline instead of
 * leaving a hole in it.
 */

export interface StreamEvent {
  id: string;
  type: string;
  ts: string;
  project_id: string;
  run_id?: string | null;
  session_id?: string | null;
  trace_id?: string | null;
  payload: Record<string, any>;
}

type Listener = (event: StreamEvent) => void;

export class EventStream {
  private socket: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private attempt = 0;
  private closed = false;
  private timer: number | undefined;

  constructor(private projectId: string) {}

  connect() {
    if (this.closed) return;
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${scheme}://${location.host}/ws/projects/${this.projectId}?replay=80`;
    const socket = new WebSocket(url);
    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.emit(this.synthetic('open'));
    };
    socket.onmessage = (raw) => {
      try {
        const event = JSON.parse(raw.data);
        if (event.type === 'ping') return;
        this.emit(event);
      } catch { /* ignore malformed frame */ }
    };
    socket.onclose = () => {
      this.emit(this.synthetic('closed'));
      this.scheduleReconnect();
    };
    socket.onerror = () => socket.close();
  }

  private scheduleReconnect() {
    if (this.closed) return;
    // Capped exponential backoff: a server restart should not produce a
    // reconnect storm from every open tab.
    const delay = Math.min(15000, 500 * 2 ** this.attempt++);
    this.timer = window.setTimeout(() => this.connect(), delay);
  }

  /** Connection-state events are synthesised locally, not sent by the server. */
  private synthetic(state: 'open' | 'closed'): StreamEvent {
    return {
      id: `conn-${state}-${Date.now()}`,
      type: '_connection',
      ts: new Date().toISOString(),
      project_id: this.projectId,
      payload: { state },
    };
  }

  private emit(event: StreamEvent) {
    this.listeners.forEach((fn) => fn(event));
  }

  subscribe(fn: Listener) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  close() {
    this.closed = true;
    window.clearTimeout(this.timer);
    this.socket?.close();
  }
}
