import { defineStore } from 'pinia';

import { subStores } from './registry';

// Side-effect imports: each of these stores registers itself with the registry,
// and pulling them in here guarantees that they always do. Otherwise
// `queryDraft`, which is otherwise loaded only by the lazy query routes, could
// stay unknown to a logout that should drop its localStorage backup.
import './store-activity';
import './store-query';
import './store-session';

/**
 * App-wide state resets, fanned out to the sub-stores that registered with the
 * `registry`. Call this instead of cleaning several stores by hand:
 *
 * ```ts
 * useMetaStore().invalidate() // the API refused our credentials (a 401)
 * useMetaStore().clear() // the app session is over (a logout)
 * ```
 *
 * A store takes part by registering itself and implementing the hook or hooks it
 * has (see `StoreCleanup` in `./registry`); that registration is all it takes to
 * be included here. The meta store owns no state and is not itself a sub-store:
 * a sub-store's hook touches only its own state, so cleanup cannot cascade back
 * in here and the order sub-stores are asked in does not matter.
 */
export const useMetaStore = defineStore('meta', {
  actions: {
    /** Asks every sub-store to forget everything it owns (a logout). */
    clear() {
      for (const useStore of subStores()) useStore().clear?.();
    },
    /** Asks every sub-store to drop what our credentials made possible (a 401). */
    invalidate() {
      for (const useStore of subStores()) useStore().invalidate?.();
    },
  },
});
