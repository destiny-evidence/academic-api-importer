import type { Pinia } from 'pinia';

import { useActivityStore } from './store-activity';
import { useMetaStore } from './store-meta';
import { useSessionStore } from './store-session';

/**
 * Resolves a store on first use, so API client plugins can be installed
 * independently of when Pinia becomes active.
 */
export function lazyStore<S extends object>(useStore: (pinia?: Pinia) => S): () => S {
  let store: S | undefined;
  return () => (store ??= useStore());
}

/**
 * The stores used from outside a component, where nothing else has guaranteed
 * Pinia to be active yet: the API client plugins post toasts, count requests and
 * hand a refused session to the meta store. A component resolves the store it
 * needs itself (`useSessionStore()`) from that store's own module, and so does a
 * store reaching for another one - importing this barrel from a store would make
 * the module graph circular at evaluation time, not just at call time.
 */
export const activity = lazyStore(useActivityStore);
export const meta = lazyStore(useMetaStore);
export const session = lazyStore(useSessionStore);
