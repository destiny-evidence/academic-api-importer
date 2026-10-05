import { defineStore } from 'pinia';

import { Users, type QueryInfo, type UserPublic } from '@/plugins/api/spec';
import { rejectMessage } from '@/plugins/api';

const STORAGE_KEY = 'aai-session';
const TOAST_HANGTIME = 8 * 1000; // time in ms before notification is removed automatically

/**
 * An API request that failed, either with an error response or without reaching
 * the API at all. Recorded by the request counter client plugin.
 */
export interface ApiError {
  /** When the failure was observed, as an ISO timestamp. */
  at: string;
  method: string;
  url: string;
  /** HTTP status, or `0` when no response was received (e.g. network error). */
  status: number;
  statusText: string;
  /**
   * The parsed response body, usually FastAPI's error payload such as
   * `{detail: '...'}`, or the thrown error for network failures.
   */
  body: unknown;
}

export interface Toast {
  key: number;
  level: ToastType;
  message: string;
  htmlMessage: string;
  bg: string;
  col: string;
}

export type ToastType = 'SUCCESS' | 'INFO' | 'ERROR' | 'WARN';
/** Bootstrap colour classes per toast level, as `ToastsViewer` renders them. */
export const LevelStyle: Record<ToastType, { bg: string; col: string }> = {
  SUCCESS: { bg: 'bg-success', col: 'text-white' },
  INFO: { bg: 'bg-info', col: 'text-dark' },
  WARN: { bg: 'bg-warning', col: 'text-dark' },
  ERROR: { bg: 'bg-danger', col: 'text-white' },
};

/** A fresh draft query for the query utils page, used when no backup exists. */
export function defaultQueryInfo(): QueryInfo {
  return {
    query: '((climate AND health) OR (adaptation OR mitigation))',
    wrapper: 'scopus',
  };
}

/** How long to wait after the last draft edit before writing the backup. */
const QUERY_INFO_PERSIST_DEBOUNCE = 250;

/** Pending debounced `persistSession` from `setQueryInfo`, if any. */
let persistTimer: ReturnType<typeof setTimeout> | undefined;

/** Drops a pending debounced backup write. */
function cancelPersist() {
  clearTimeout(persistTimer);
  persistTimer = undefined;
}

/** The session persisted in localStorage, if any. */
function loadStoredSession(): {
  token: string | null;
  user: UserPublic | null;
  queryInfo: QueryInfo | null;
} {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const stored = raw ? JSON.parse(raw) : null;
    return {
      token: stored?.token ?? null,
      user: stored?.user ?? null,
      queryInfo: stored?.queryInfo ?? null,
    };
  } catch {
    return { token: null, user: null, queryInfo: null };
  }
}

/**
 * Session state backed by localStorage.
 *
 * Login is handled by `POST /users/login` (through the generated API client, so
 * the request counter plugin sees it), which validates credentials and sets the
 * `aai_user_token` cookie itself. That cookie is HttpOnly, so it cannot be read
 * from JavaScript; the session is tracked from the returned access token and sent
 * to the API as a bearer token (the cookie also travels with credentialed
 * same-origin requests). The logged-in user's profile is fetched from
 * `/users/me` right after login and stored next to the token; `restore()`
 * re-syncs these pieces with the API when the app boots from storage.
 */
export const useSessionStore = defineStore('session', {
  state: () => {
    const stored = loadStoredSession();
    return {
      token: stored.token,
      /** Profile of the logged-in user, fetched from `/users/me` during login. */
      user: stored.user,
      /**
       * Draft query of the utils page, edited through `setQueryInfo` so that it
       * is backed up on every change and survives a closed tab.
       */
      queryInfo: stored.queryInfo ?? defaultQueryInfo(),
      /** Requests sent by the API client that have not settled yet. */
      pendingRequests: 0,
      /** Requests that failed, oldest first. */
      errors: [] as ApiError[],
      toasts: {} as Record<number, Toast>,
      runningToastCount: 0 as number, // used to generate unique "IDs" for toasts
    };
  },
  getters: {
    isAuthenticated: (state) => Boolean(state.token),
    isBusy: (state) => state.pendingRequests > 0,
  },
  actions: {
    /**
     * Authenticate with email and password; persists the returned access token
     * and the user profile fetched with it.
     */
    async login(email: string, password: string) {
      // The error is kept in the result instead of thrown so that its `detail` can
      // be reported; the client plugin still records the failure in `errors`.
      const { data, error } = await Users.loginUsersLoginPost(
        {
          loginRequest: { email, password },
        },
        {
          credentials: 'include',
          throwOnError: false,
        },
      );
      if (!data) throw new Error(rejectMessage(error) || 'Login failed');

      this.persist(data.access_token);

      // Fetch the profile with the fresh token (the auth plugin attaches it).
      // A session without user information is not usable, so a failure here
      // drops the session just like a failed login would.
      const { data: user, error: userError } = await Users.getMyInfoUsersMeGet(undefined, {
        credentials: 'include',
        throwOnError: false,
      });
      if (!user) {
        this.invalidate();
        throw new Error(rejectMessage(userError) || 'Could not fetch user information');
      }
      this.setUser(user);
    },
    /**
     * Re-syncs the session with what the browser can still prove about it;
     * called once at startup (from `main.ts`, after the API client plugins are
     * installed). Login fetches token and profile together, so a session read
     * back from storage can only be missing one of them:
     *
     * - Token survived, profile did not (e.g. the tab was closed during
     *   login): the profile is fetched again, and a token the API no longer
     *   accepts is dropped just like a failed login would.
     * - No token, but the HttpOnly cookie may still be valid (localStorage
     *   lost): a session cannot be rebuilt from a cookie whose value is
     *   unreadable, so the cookie is dropped and the user fully logs out
     *   instead of the two halves of the session drifting apart.
     */
    async restore() {
      if (!this.token) {
        // A stored profile without its token can neither be refreshed nor
        // authenticate anything; treat it as part of the lost session.
        if (this.user) this.invalidate();
        await this.dropStaleCookie();
        return;
      }
      if (this.user) return;

      const { data: user } = await Users.getMyInfoUsersMeGet(undefined, {
        credentials: 'include',
        throwOnError: false,
      });
      if (user) this.setUser(user);
      else this.invalidate();
    },
    /**
     * Asks the API to clear its auth cookie, for a cookie that outlived the
     * local session. The request authenticates with the cookie itself; when
     * there is none (or it has expired), the API simply refuses it and there
     * was no half-session to begin with.
     */
    async dropStaleCookie() {
      await Users.logoutUsersLogoutPost(undefined, {
        credentials: 'include',
        throwOnError: false,
      });
    },
    /** Ask the backend to clear the auth cookie, then drop the local session. */
    async logout() {
      try {
        await Users.logoutUsersLogoutPost(
          { authorization: this.token },
          { credentials: 'include', throwOnError: false },
        );
      } finally {
        // Clear local state even if the request failed (e.g. cookie expired).
        this.clear();
      }
    },
    persist(token: string) {
      this.token = token;
      this.user = null;
      this.persistSession();
    },
    /** Remembers the logged-in user's profile, fetched from `/users/me`. */
    setUser(user: UserPublic) {
      this.user = user;
      this.persistSession();
    },
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
        this.persistSession();
      }, QUERY_INFO_PERSIST_DEBOUNCE);
    },
    /** Writes the current token, user, and draft query to localStorage. */
    persistSession() {
      // The synchronous write covers any pending debounced one.
      cancelPersist();
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ token: this.token, user: this.user, queryInfo: this.queryInfo }),
      );
    },
    clear() {
      this.invalidate();
      // Recorded failures belong to the session that just ended.
      this.clearErrors();
    },
    /**
     * Drops a session the API refused to authenticate, keeping recorded failures
     * so the UI can still explain why the user was signed out.
     */
    invalidate() {
      // A debounced draft write must not resurrect the session just dropped.
      cancelPersist();
      this.token = null;
      this.user = null;
      // The draft query is part of the session's backup, so it resets with it.
      this.queryInfo = defaultQueryInfo();
      localStorage.removeItem(STORAGE_KEY);
    },
    /** Notes that a request was handed to the API client; pair with `endRequest`. */
    beginRequest() {
      this.pendingRequests += 1;
    },
    /** Notes that a request settled, never dropping below zero. */
    endRequest() {
      this.pendingRequests = Math.max(0, this.pendingRequests - 1);
    },
    /** Keeps a failed response around, e.g. to report it to the user. */
    recordError(error: ApiError) {
      this.errors.push(error);
    },
    clearErrors() {
      this.errors = [];
    },
    toast(toast: Omit<Toast, 'key'>): number {
      const toastId = this.runningToastCount;
      // increment counter to keep track of unique "IDs" for toasts
      this.runningToastCount += 1;
      // add toast to store; the key is assigned here so callers can omit it
      this.toasts[toastId] = { ...toast, key: toastId };
      // set up timer to automatically remove toast after hangtime
      setTimeout(() => this.removeToast(toastId), TOAST_HANGTIME);
      return this.runningToastCount;
    },
    removeToast(key: number) {
      delete this.toasts[key];
    },
  },
});

/**
 * Resolves the session store on first use, so plugins can be installed
 * independently of when Pinia becomes active.
 */
export function lazySessionStore(): () => ReturnType<typeof useSessionStore> {
  let store: ReturnType<typeof useSessionStore> | undefined;
  return () => (store ??= useSessionStore());
}

// Flush a debounced draft backup when the page goes away (closing the tab or
// a bfcache navigation). If a timer is pending, a draft edit scheduled it, so
// the store exists and Pinia is active by now.
window.addEventListener('pagehide', () => {
  if (persistTimer !== undefined) useSessionStore().persistSession();
});
