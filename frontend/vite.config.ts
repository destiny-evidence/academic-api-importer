import { fileURLToPath, URL } from 'node:url';
import type { IncomingMessage } from 'node:http';

import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vite';

// Backend the dev server proxies API calls to (e.g. `uv run fastapi dev`).
const apiTarget = process.env.VITE_API_TARGET || 'http://localhost:8000';

// The SPA routes share paths with the API (/queries, /tasks, /tokens). Serve the
// app for browser page loads (Accept: text/html) and proxy everything else
// (fetch/XHR) so dev requests stay same-origin and the auth cookie works.
function bypassHtml(req: IncomingMessage): string | undefined {
  if (req.headers.accept?.includes('text/html')) return '/index.html';
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/users': { target: apiTarget, changeOrigin: true },
      '/queries': { target: apiTarget, changeOrigin: true, bypass: bypassHtml },
      '/tasks': { target: apiTarget, changeOrigin: true, bypass: bypassHtml },
      '/tokens': { target: apiTarget, changeOrigin: true, bypass: bypassHtml },
    },
  },
});
