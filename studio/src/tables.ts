// Turns raw audit events into the tables AG Studio's data engine works on.
import type { LogEvent } from './logs';

export const WARN = 1.0, BLOCK = 2.5, SCALE = 3.5;
export type Level = 'ok' | 'warn' | 'block';
export const riskLevel = (r: number): Level => (r >= BLOCK ? 'block' : r >= WARN ? 'warn' : 'ok');

const num = (v: unknown): number | null => { const n = parseFloat(String(v)); return Number.isFinite(n) ? n : null; };
const short = (v: unknown, n: number) => { const s = typeof v === 'string' ? v : JSON.stringify(v) ?? ''; return s.length > n ? s.slice(0, n - 1) + '\u2026' : s; };
const id8 = (v: unknown) => String(v ?? '').slice(0, 8);
export const safeUrl = (u: unknown): string | null => (typeof u === 'string' && u.startsWith('https://') ? u : null);
const clock = (ts: number | null) => { if (ts == null) return ''; const d = new Date(ts * 1000); const p = (n: number) => String(n).padStart(2, '0'); return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`; };

const LABEL: Record<string, string> = {
  session_start: 'Session opened', user_message: 'You asked', tool_call: 'Agent called',
  search_results: 'Products found', decision: 'Gate decision', step_up_requested: 'Approval needed', step_up_resolved: 'Approval answered',
  captured: 'Payment made', paypal_error: 'Provider error', catalog_error: 'Provider error', charge_mismatch: 'Charge mismatch'
};
const ACTION: Record<string, string> = { create_order: 'Order', capture: 'Capture', charge_saved: 'Charge' };
const VERDICT_TEXT: Record<string, string> = {
  paid: 'Paid', allow: 'Allowed', step_up: 'Needs approval', deny: 'Blocked',
  approved: 'Approved', denied: 'Denied', expired: 'Expired', error: 'Error'
};

function describe(ev: LogEvent): { verdict: string; summary: string } {
  const d = ev.data ?? {};
  switch (ev.event) {
    case 'session_start': return { verdict: '', summary: `Mandate: up to ${d.limit} ${d.currency ?? ''} at ${(d.merchants ?? []).join(', ')}${d.auto ? ' (auto-pay)' : ''}` };
    case 'user_message': return { verdict: '', summary: short(d.text, 200) };
    case 'tool_call': return { verdict: '', summary: `${d.tool}(${short(d.args, 90)})` };
    case 'search_results': return { verdict: '', summary: `${Array.isArray(d.items) ? d.items.length : 0} offers for "${short(d.query, 60)}"` };
    case 'decision': return { verdict: d.verdict ?? '', summary: `${ACTION[d.action] ?? d.action}: ${d.reason}${d.semantic_drift > 0 ? `, intent drift ${d.semantic_drift}` : ''}` };
    case 'step_up_requested': return { verdict: 'step_up', summary: `Waiting for you to approve (request ${id8(d.request_id)})` };
    case 'step_up_resolved': return { verdict: d.status ?? '', summary: `Request ${id8(d.request_id)}: ${d.status}` };
    case 'captured': return { verdict: 'paid', summary: `Order ${d.order_id}, capture ${d.capture_id}${d.auto ? ' (auto-pay)' : ''}` };
    case 'paypal_error': return { verdict: 'error', summary: `${d.action} failed: ${short(d.detail ?? d.status, 160)}` };
    case 'catalog_error': return { verdict: 'error', summary: `Catalog search failed: ${short(d.detail ?? d.status, 160)}` };
    case 'charge_mismatch': return { verdict: 'error', summary: `Expected ${d.expected}, charged ${d.got}` };
    default: return { verdict: '', summary: short(d, 120) };
  }
}

export interface Product { item_id: string; name: string; merchant: string; price: number; image: string | null; query: string; in_mandate: string; }
export interface Pending { request_id: string; seq: number; }
export interface Summary { id: string; paid: number; blocked: number; waiting: number; events: number; risk: number; limit: number; }
export interface Tables {
  events: Record<string, unknown>[]; decisions: Record<string, unknown>[]; payments: Record<string, unknown>[];
  risk: Record<string, unknown>[]; products: Product[]; summary: Summary[]; pending: Pending[];
  riskNow: number; level: Level; paid: number; limit: number | null; currency: string; merchants: string[];
}

export function buildTables(events: LogEvent[]): Tables {
  const t: Tables = {
    events: [], decisions: [], payments: [], risk: [], products: [], summary: [], pending: [],
    riskNow: 0, level: 'ok', paid: 0, limit: null, currency: '', merchants: []
  };
  let blocked = 0, merchants: string[] | null = null;
  const requested = new Map<string, number>(), resolved = new Set<string>(), seen = new Set<string>();
  for (const ev of events) {
    const d = ev.data ?? {};
    const { verdict, summary } = describe(ev);
    t.events.push({
      seq: ev.seq, time: clock(ev.ts), event: LABEL[ev.event] ?? ev.event, result: VERDICT_TEXT[verdict] ?? verdict,
      summary, amount: num(d.total) ?? 0, risk: typeof d.risk === 'number' ? d.risk : 0
    });
    if (ev.event === 'session_start') { t.limit = num(d.limit); t.currency = d.currency ?? ''; merchants = d.merchants ?? null; t.merchants = merchants ?? []; }
    if (typeof d.risk === 'number') { t.riskNow = d.risk; t.risk.push({ seq: ev.seq, risk: d.risk, warn: WARN, block: BLOCK }); }
    if (ev.event === 'decision') {
      if (d.verdict === 'deny') blocked += 1;
      t.decisions.push({
        seq: ev.seq, action: ACTION[d.action] ?? d.action ?? '', verdict: VERDICT_TEXT[d.verdict] ?? d.verdict ?? '',
        reason: d.reason ?? '', amount: num(d.total) ?? 0, risk: typeof d.risk === 'number' ? d.risk : 0, drift: num(d.semantic_drift) ?? 0
      });
    }
    if (ev.event === 'captured') {
      t.paid += num(d.total) ?? 0;
      t.payments.push({ seq: ev.seq, order_id: String(d.order_id ?? ''), amount: num(d.total) ?? 0, cumulative: Math.round(t.paid * 100) / 100 });
    }
    if (ev.event === 'step_up_requested') requested.set(String(d.request_id), ev.seq);
    if (ev.event === 'step_up_resolved') resolved.add(String(d.request_id));
    if (ev.event === 'search_results' && Array.isArray(d.items)) {
      for (const it of d.items) {
        if (!it || seen.has(it.item_id)) continue;
        seen.add(it.item_id);
        t.products.push({
          item_id: String(it.item_id), name: String(it.name ?? ''), merchant: String(it.merchant ?? ''), price: num(it.price) ?? 0,
          image: safeUrl(it.image), query: String(d.query ?? ''), in_mandate: ''
        });
      }
    }
  }
  t.products.forEach((p) => { p.in_mandate = merchants ? (merchants.includes(p.merchant) ? 'Yes' : 'No') : 'Unknown'; });
  t.products.reverse();
  requested.forEach((seq, id) => { if (!resolved.has(id)) t.pending.push({ request_id: id, seq }); });
  t.level = riskLevel(t.riskNow);
  t.summary = [{ id: 'now', paid: Math.round(t.paid * 100) / 100, blocked, waiting: t.pending.length, events: events.length, risk: t.riskNow, limit: t.limit ?? 0 }];
  return t;
}
