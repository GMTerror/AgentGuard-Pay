import { studioTheme } from 'ag-studio';

const dark = window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
export const theme = studioTheme.withParams(dark
  ? { backgroundColor: '#141e2b', foregroundColor: '#e8eef5', accentColor: '#7aa2ff', borderColor: '#243245', fontFamily: 'Segoe UI Variable Text, Segoe UI, system-ui, sans-serif' }
  : { backgroundColor: '#fbfcfd', foregroundColor: '#0f1b2b', accentColor: '#1f4fd8', borderColor: '#cfd7df', fontFamily: 'Segoe UI Variable Text, Segoe UI, system-ui, sans-serif' } as any);
