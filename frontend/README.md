# Academic API Importer — frontend

Minimal Vue 3 + Vite single-page app for the academic-api-importer backend.

## Develop

```bash
npm install
npm run dev
```

The dev server proxies `/users`, `/queries`, `/tasks`, `/tokens` and `/profile`
to the backend (`http://localhost:8000` by default, override with
`VITE_API_TARGET`), so run `uv run fastapi dev` in the repo root alongside.

## Build

```bash
npm run build   # output in dist/
npm run preview
```

### Serving under a path / other origin

All of these are build-time environment variables (also set as CI/CD
variables, e.g. `AAPI_VITE_API_URL=https://domain.com/aapi-api npm run build`;
a `.env.local` file works locally):

- `AAPI_VITE_UI_URL`: URL (or bare path) the frontend is served under, e.g.
  `https://domain.com/aapi/` or `/aapi/`. Only the path part is used: it
  becomes Vite's `base`, so built asset and favicon URLs and the Vue router
  (via `import.meta.env.BASE_URL`) all live under it. Copy `dist/` into that
  directory on the web server. Defaults to the site root (`/`).
- `AAPI_VITE_API_URL`: root of the API, absolute (`https://api.domain.com` or
  `https://domain.com/aapi-api`) or relative to the origin (`/aapi-api`). It
  becomes the generated client's `baseUrl` (`src/plugins/api/base-url.ts`);
  without it, API requests go to the same path the frontend is served under —
  with the default `AAPI_VITE_UI_URL` that means plain `/queries` requests,
  as before.
- `AAPI_VITE_HELP_URL`: URL of the help documentation (e.g. the docs site),
  linked as **Help** in the navigation bar. Without it, the navbar shows no
  help link.
- `VITE_API_TARGET`: dev-server proxy target only; irrelevant to builds.

The API endpoints themselves never move: when the backend runs on a different
origin, configure its CORS with `AAI_WEB_ORIGINS`; when it sits behind a
reverse proxy under a path of its own, set `AAI_ROOT_PATH` accordingly. In
dev mode, requests to `<base>queries` (etc.) are proxied to `VITE_API_TARGET`
with the base prefix stripped, so the backend stays at the root.

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
