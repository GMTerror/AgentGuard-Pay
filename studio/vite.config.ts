import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/studio/',
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8800' } },
  build: { outDir: 'dist', chunkSizeWarningLimit: 6000 },
});
