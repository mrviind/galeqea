/**
 * NDJSON control protocol between the Python supervisor and this runner.
 *
 * stdout carries events (one JSON object per line); stdin carries replies. The
 * split matters: the runner can *ask questions mid-run* - "this locator broke,
 * what should I use instead?", "a human needs to take this browser over" - and
 * block until the supervisor answers. That request/response channel is what
 * makes intent-based healing and pause-and-attach handover possible without the
 * runner needing any knowledge of models, databases or approval rules.
 */

import readline from 'node:readline';

let seq = 0;
const pending = new Map();

/** Emit one event. Never throws: a broken pipe must not kill a live browser. */
export function emit(type, payload = {}) {
  const line = JSON.stringify({ type, seq: seq++, ts: new Date().toISOString(), ...payload });
  try {
    process.stdout.write(line + '\n');
  } catch {
    /* supervisor went away; the run will be reaped */
  }
}

export function log(level, message, extra = {}) {
  emit('log', { level, message, ...extra });
}

/**
 * Ask the supervisor something and wait for its reply.
 * `timeoutMs` bounds the wait so a dead supervisor cannot hang a browser forever.
 */
export function ask(type, payload = {}, timeoutMs = 120000) {
  const requestId = `req_${seq}_${Math.random().toString(36).slice(2, 8)}`;
  emit(type, { ...payload, requestId, awaitingResponse: true });

  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      pending.delete(requestId);
      resolve({ ok: false, reason: 'timeout' });
    }, timeoutMs);

    pending.set(requestId, (response) => {
      clearTimeout(timer);
      pending.delete(requestId);
      resolve(response);
    });
  });
}

export function startResponseReader() {
  const rl = readline.createInterface({ input: process.stdin, terminal: false });
  rl.on('line', (line) => {
    if (!line.trim()) return;
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      return;
    }
    const handler = pending.get(msg.requestId);
    if (handler) handler(msg);
    else if (msg.type === 'cancel') process.emit('galeqea:cancel', msg);
  });
  return rl;
}
