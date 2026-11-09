// Live audit-log store: polls /api/logs every 2 s and notifies subscribers. No page reloads.
export interface LogEvent { seq: number; ts: number | null; event: string; hash: string; data: Record<string, any>; }
export interface Snapshot { file: string | null; files: string[]; chainOk: boolean | null; events: LogEvent[]; online: boolean; updated: number; }

const POLL_MS = 2000;
let snap: Snapshot = { file: null, files: [], chainOk: null, events: [], online: false, updated: 0 };
const listeners = new Set<() => void>();
let after = -1;
let chosen = 'auto';
let inflight = false;
let timer: number | undefined;

export const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };
export const getSnapshot = () => snap;
const set = (next: Snapshot) => { snap = next; listeners.forEach((l) => l()); };

async function poll() {
  if (inflight) return;
  inflight = true;
  try {
    const q = new URLSearchParams({ file: chosen, for: snap.file ?? '', after: String(after) });
    const res = await fetch(`/api/logs?${q}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const d = await res.json();
    let events = snap.events;
    if (d.file !== snap.file) { events = []; after = -1; }
    if (d.events.length) {
      events = events.concat(d.events);
      after = d.events[d.events.length - 1].seq;
    }
    set({ file: d.file, files: d.files ?? [], chainOk: d.chain_ok, events, online: true, updated: Date.now() });
  } catch {
    set({ ...snap, online: false });
  } finally {
    inflight = false;
  }
}

export const getChosen = () => chosen;
export function selectFile(name: string) {
  chosen = name;
  void poll();
}

export function startPolling() {
  if (timer !== undefined) return;
  void poll();
  timer = window.setInterval(poll, POLL_MS);
}
