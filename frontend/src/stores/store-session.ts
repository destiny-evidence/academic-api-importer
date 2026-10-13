import { defineStore } from 'pinia';

import { Users, type UserPublic } from '@/plugins/api/spec';
import { rejectMessage } from '@/plugins/api';

import { registerSubStore } from './registry';
import { useMetaStore } from './store-meta';

const STORAGE_KEY = 'aai-session';

/** The session persisted in localStorage, if any. */
function loadStoredSession(): { token: string | null; user: UserPublic | null } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const stored = raw ? JSON.parse(raw) : null;
    return {
      token: stored?.token ?? null,
      user: stored?.user ?? null,
    };
  } catch {
    return { token: null, user: null };
  }
}

/**
 * Who is logged in, backed by localStorage.
 *
 * Login is handled by `POST /users/login` (through the generated API client, so
 * the request counter plugin sees it), which validates credentials and sets the
 * `aai_user_token` cookie itself. That cookie is HttpOnly, so it cannot be read
 * from JavaScript; the session is tracked from the returned access token and sent
 * to the API as a bearer token (the cookie also travels with credentialed
 * same-origin requests). The logged-in user's profile is fetched from
 * `/users/me` right after login and stored next to the token; `restore()`
 * re-syncs these pieces with the API when the app boots from storage.
 *
 * When the session ends, what went with it (the request failures recorded for
 * it, the draft query) is cleaned up through the meta store rather than by
 * reaching into the stores that own those pieces; see its `clear` and
 * `invalidate` actions below. `useMetaStore()` is called inside the actions,
 * never at module scope: the meta store imports this module to make sure it is
 * loaded, so this one may only look something up in it once evaluating is done.
 */
export const useSessionStore = defineStore('session', {
  state: () => {
    const stored = loadStoredSession();
    return {
      token: stored.token,
      /** Profile of the logged-in user, fetched from `/users/me` during login. */
      user: stored.user,
    };
  },
  getters: {
    isAuthenticated: (state) => Boolean(state.token),
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
        useMetaStore().invalidate();
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
        if (this.user) useMetaStore().invalidate();
        await this.dropStaleCookie();
        return;
      }
      if (this.user) return;

      const { data: user } = await Users.getMyInfoUsersMeGet(undefined, {
        credentials: 'include',
        throwOnError: false,
      });
      if (user) this.setUser(user);
      else useMetaStore().invalidate();
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
        // Clear local state even if the request failed (e.g. cookie expired);
        // everything the session was good for goes with it, not just this store.
        useMetaStore().clear();
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
    /** Writes the current token and user to localStorage. */
    persistSession() {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: this.token, user: this.user }));
    },
    /**
     * The session's share of an app-wide reset (see `StoreCleanup`): for the
     * session itself both verbs mean the same thing - there is no half of a
     * session worth keeping without its token - and what differs is what the
     * other stores keep, which each of them decides for itself.
     */
    clear() {
      this.invalidate();
    },
    /** Drops our credentials and the profile fetched with them. */
    invalidate() {
      this.token = null;
      this.user = null;
      localStorage.removeItem(STORAGE_KEY);
    },
  },
});

registerSubStore(useSessionStore);
