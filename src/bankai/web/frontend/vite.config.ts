import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Build the SPA directly into the package's static dir so FastAPI can
// serve it. The output is committed to the repo so the server never
// needs Node.js.
const api = process.env.BANKAI_API ?? 'http://localhost:9988';

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
  // BANKAI_API points the dev server at a running bankai, e.g.
  // http://seireitei:7003, to work on the UI against real data.
  server: {
    proxy: {
      '/api': { target: api, changeOrigin: true },
      '/ws': { target: api.replace(/^http/, 'ws'), ws: true, changeOrigin: true },
    },
  },
});
