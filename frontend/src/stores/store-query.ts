import { defineStore } from 'pinia';

import type { QueryInfo } from '@/plugins/api/spec';

import { registerSubStore } from './registry';

const STORAGE_KEY = 'aai-query-draft';
/** How long to wait after the last draft edit before writing the backup. */
const PERSIST_DEBOUNCE = 250;

/** Pending debounced `persist` from `setQueryInfo`, if any. */
let persistTimer: ReturnType<typeof setTimeout> | undefined;

/** Drops a pending debounced backup write. */
function cancelPersist() {
  clearTimeout(persistTimer);
  persistTimer = undefined;
}

/** A fresh draft query for the query utils page, used when no backup exists. */
export function defaultQueryInfo(): QueryInfo {
  return {
    query: 'TITLE-ABS((climate AND health) OR (adaptation OR mitigation))',
    wrapper: 'scopus',
  };
}

/** The draft query persisted in localStorage, if any. */
function loadStoredQueryInfo(): QueryInfo | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const stored = raw ? JSON.parse(raw) : null;
    return stored?.queryInfo ?? null;
  } catch {
    return null;
  }
}

/**
 * The query utils page's draft query, backed by localStorage so that it survives
 * a closed tab. Kept apart from the session because a draft is a convenience
 * rather than state the app depends on; it just happens to be backed up while a
 * session is around, so the meta store hands it the resets that end one. Nothing
 * else about it travels to the API.
 */
export const useQueryDraftStore = defineStore('queryDraft', {
  state: () => ({
    /**
     * Draft query of the utils page, edited through `setQueryInfo` so that it
     * is backed up on every change and survives a closed tab.
     */
    queryInfo: loadStoredQueryInfo() ?? defaultQueryInfo(),
  }),
  actions: {
    /**
     * Backs up the draft query (utils page), coalescing rapid edits (e.g.
     * keystrokes) into one localStorage write; `pagehide` below flushes a
     * pending write so closing the tab never loses the draft.
     */
    setQueryInfo(queryInfo: QueryInfo) {
      this.queryInfo = queryInfo;
      cancelPersist();
      persistTimer = setTimeout(() => {
        persistTimer = undefined;
        this.persist();
      }, PERSIST_DEBOUNCE);
    },
    /** Writes the current draft query to localStorage. */
    persist() {
      // The synchronous write covers any pending debounced one.
      cancelPersist();
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ queryInfo: this.queryInfo }));
    },
    /**
     * The app session that was editing this draft is over, and the draft was
     * backed up with it, so it goes too.
     */
    clear() {
      // A debounced write must not resurrect the draft just dropped.
      cancelPersist();
      this.queryInfo = defaultQueryInfo();
      localStorage.removeItem(STORAGE_KEY);
    },
    /** A draft is not worth keeping once the credentials behind it failed. */
    invalidate() {
      this.clear();
    },
  },
});

registerSubStore(useQueryDraftStore);

// Flush a debounced draft backup when the page goes away (closing the tab or
// a bfcache navigation). If a timer is pending, a draft edit scheduled it, so
// the store exists and Pinia is active by now.
window.addEventListener('pagehide', () => {
  if (persistTimer !== undefined) useQueryDraftStore().persist();
});
