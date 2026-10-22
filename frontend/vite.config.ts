import { fileURLToPath, URL } from 'node:url';
import type { IncomingMessage } from 'node:http';

import vue from '@vitejs/plugin-vue';
import { defineConfig, loadEnv } from 'vite';

// The SPA routes share paths with the API (/queries, /tasks, /tokens). Serve the
// app for browser page loads (Accept: text/html) and proxy everything else
// (fetch/XHR) so dev requests stay same-origin and the auth cookie works.
function bypassHtml(req: IncomingMessage): string | undefined {
  if (req.headers.accept?.includes('text/html')) return '/index.html';
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const apiTarget = env.AAPI_VITE_API_URL || 'http://localhost:8000';

  return {
    plugins: [vue()],
    envPrefix: ['AAPI_'],
    base: env.AAPI_VITE_UI_URL,
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      proxy: {
        '/users': { target: apiTarget, changeOrigin: true, bypass: bypassHtml },
        '/queries': { target: apiTarget, changeOrigin: true, bypass: bypassHtml },
        '/tasks': { target: apiTarget, changeOrigin: true, bypass: bypassHtml },
        '/tokens': { target: apiTarget, changeOrigin: true, bypass: bypassHtml },
        '/profile': { target: apiTarget, changeOrigin: true, bypass: bypassHtml },
        '/ingest': { target: apiTarget, changeOrigin: true, bypass: bypassHtml },
      },
    },
    define: {
      AAPI_VITE_API_URL: JSON.stringify(env.AAPI_VITE_API_URL || ''),
      AAPI_VITE_UI_URL: JSON.stringify(env.AAPI_VITE_UI_URL || ''),
      AAPI_VITE_HELP_URL: JSON.stringify(env.AAPI_VITE_HELP_URL || ''),
    },
  };
});
