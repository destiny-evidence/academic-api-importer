import type { Pinia } from 'pinia';

/**
 * The cleanup a sub-store contributes to an app-wide reset. Both hooks are
 * optional: a store implements only what it actually has to drop, and one that
 * implements neither simply has nothing to clean up.
 */
export interface StoreCleanup {
  /** The app session is over (a logout): forget everything you own. */
  clear?(): void;
  /**
   * The API refused our credentials: forget what depended on them, but keep
   * whatever can still explain the sign-out. This is why `activity` has no
   * `invalidate()` - the failures it recorded are that explanation.
   */
  invalidate?(): void;
}

/** How the meta store reaches a sub-store: its accessor, called on demand. */
type SubStore = (pinia?: Pinia) => StoreCleanup;

/** Everything that takes part in a reset, in the order it registered. */
const participants: SubStore[] = [];

/**
 * Takes part in app-wide resets, called by a sub-store right after it defines
 * itself; the meta store (see `./store-meta`) then calls its hooks. Registering
 * rather than being listed by the meta store is what makes this safe to call
 * while module evaluation is still going on: a store's body runs only after
 * everything it imports has been evaluated, whereas the meta store has to import
 * the stores to make sure they are loaded, and so cannot be imported by them and
 * hold the list at the same time.
 */
export function registerSubStore(useStore: SubStore): void {
  participants.push(useStore);
}

/** The registered sub-stores, in the order they will be asked to clean up. */
export function subStores(): readonly SubStore[] {
  return participants;
}
