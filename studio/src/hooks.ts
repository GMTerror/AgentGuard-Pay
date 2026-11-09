import { useMemo, useSyncExternalStore } from 'react';
import { getSnapshot, subscribe } from './logs';
import { buildTables } from './tables';
import type { LogEvent } from './logs';

let lastEvents: LogEvent[] | null = null;
let lastTables: ReturnType<typeof buildTables> | null = null;
export function tablesFor(events: LogEvent[]) {
  if (events !== lastEvents || !lastTables) { lastEvents = events; lastTables = buildTables(events); }
  return lastTables;
}
export function useSnapshot() { return useSyncExternalStore(subscribe, getSnapshot); }
export function useTables() { const s = useSnapshot(); return useMemo(() => tablesFor(s.events), [s.events]); }
