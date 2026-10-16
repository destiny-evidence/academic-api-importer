/// <reference types="vite/client" />

/**
 * URL (or bare path) the frontend is served under, e.g.
 * `https://domain.com/aapi/`. Only its path part is used; it becomes Vite's
 * `base` and `import.meta.env.BASE_URL`. Defaults to the site root (`/`).
 * Read at build time by `vite.config.ts`.
 */
declare const AAPI_VITE_UI_URL: string | undefined;

/**
 * Root URL the API is reachable at, e.g. `https://api.domain.com` or
 * `/aapi-api`. Defaults to the path the frontend is served under.
 * See `src/plugins/api/base-url.ts`.
 */

declare const AAPI_VITE_API_URL: string | undefined;

/**
 * URL of the help documentation, linked from the navigation bar. The link is
 * omitted when unset. Read at build time by `vite.config.ts`.
 */
declare const AAPI_VITE_HELP_URL: string | undefined;
