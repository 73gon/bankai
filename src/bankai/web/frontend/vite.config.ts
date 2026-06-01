import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Build the SPA directly into the package's static dir so FastAPI can
// serve it. The output is committed to the repo so the server never
// needs Node.js.
export default defineConfig({
  plugins: [react()],
  base: '/',
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  build: {
    outDir: path.resolve(__dirname, '../static'),
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:9988',
      '/ws': { target: 'ws://localhost:9988', ws: true },
    },
  },
});
