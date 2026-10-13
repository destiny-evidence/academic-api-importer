# Academic API Importer — frontend

Minimal Vue 3 + Vite single-page app for the academic-api-importer backend.

## Develop

```bash
npm install
npm run dev
```

The dev server proxies `/set-cookie`, `/queries`, `/tasks` and `/tokens` to the
backend (`http://localhost:8000` by default, override with `VITE_API_TARGET`),
so run `uv run fastapi dev` in the repo root alongside.

## Build

```bash
npm run build   # output in dist/
npm run preview
```

## Lint, format & type-check

```bash
npm run lint          # ESLint (flat config: eslint.config.js)
npm run lint:fix      # ESLint with autofix
npm run format        # Prettier (config: .prettierrc.json)
npm run format:check  # Prettier check only
npm run typecheck     # vue-tsc --build (tsconfig.app.json + tsconfig.node.json)
```

Sources are TypeScript: `tsconfig.app.json` covers `src/` (strict mode,
`@/*` alias, vite client types) and `tsconfig.node.json` covers
`vite.config.ts`; the root `tsconfig.json` ties them together for
`vue-tsc --build`.

## Auth flow

- The router redirects to `/login` whenever the `aai_auth_token` cookie is missing.
- The login page posts the entered token to the backend's `/set-cookie` endpoint,
  which validates it and sets the cookie.
- The token is mirrored into a Pinia session store persisted in localStorage
  (`aai-session`). The cookie stays the source of truth: if it disappears
  (expired/cleared), the stored session is discarded on the next navigation.
