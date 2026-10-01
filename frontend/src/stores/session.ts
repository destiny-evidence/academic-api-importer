import { defineStore } from 'pinia'

import { Users } from '@/plugins/api/spec'

const STORAGE_KEY = 'aai-session'
const TOAST_HANGTIME = 8 * 1000 // time in ms before notification is removed automatically

/**
 * An API request that failed, either with an error response or without reaching
 * the API at all. Recorded by the request counter client plugin.
 */
export interface ApiError {
  /** When the failure was observed, as an ISO timestamp. */
  at: string
  method: string
  url: string
  /** HTTP status, or `0` when no response was received (e.g. network error). */
  status: number
  statusText: string
  /**
   * The parsed response body, usually FastAPI's error payload such as
   * `{detail: '...'}`, or the thrown error for network failures.
   */
  body: unknown
}

export interface Toast {
  key: number
  level: ToastType
  message: string
  htmlMessage: string
  bg: string
  col: string
}

export type ToastType = 'SUCCESS' | 'INFO' | 'ERROR' | 'WARN'

/** The access token persisted in localStorage, if any. */
function loadStoredToken(): string | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw).token ?? null) : null
  } catch {
    return null
  }
}

/**
 * Human-readable message of an API error payload, e.g. FastAPI's
 * `{detail: 'Inactive user'}`. Returns an empty string when there is nothing to
 * show, such as a validation error or a network failure.
 */
function errorDetail(error: unknown): string {
  if (typeof error === 'string') return error
  if (error && typeof error === 'object' && 'detail' in error) {
    const { detail } = error as { detail?: unknown }
    if (typeof detail === 'string') return detail
  }
  return ''
}

/**
 * Session state backed by localStorage.
 *
 * Login is handled by `POST /users/login` (through the generated API client, so
 * the request counter plugin sees it), which validates credentials and sets the
 * `aai_user_token` cookie itself. That cookie is HttpOnly, so it cannot be read
 * from JavaScript; the session is tracked from the returned access token and sent
 * to the API as a bearer token (the cookie also travels with credentialed
 * same-origin requests).
 */
export const useSessionStore = defineStore('session', {
  state: () => ({
    token: loadStoredToken(),
    /** Requests sent by the API client that have not settled yet. */
    pendingRequests: 0,
    /** Requests that failed, oldest first. */
    errors: [] as ApiError[],
    toasts: {} as Record<number, Toast>,
    runningToastCount: 0 as number, // used to generate unique "IDs" for toasts
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.token),
    isBusy: (state) => state.pendingRequests > 0,
  },
  actions: {
    /** Authenticate with email and password; persists the returned access token. */
    async login(email: string, password: string) {
      // The error is kept in the result instead of thrown so that its `detail` can
      // be reported; the client plugin still records the failure in `errors`.
      const { data, error } = await Users.loginUsersLoginPost({
        body: { email, password },
        credentials: 'include',
        throwOnError: false,
      })
      if (!data) throw new Error(errorDetail(error) || 'Login failed')

      this.persist(data.access_token)
    },
    /** Ask the backend to clear the auth cookie, then drop the local session. */
    async logout() {
      try {
        await Users.logoutUsersLogoutPost({ credentials: 'include', throwOnError: false })
      } finally {
        // Clear local state even if the request failed (e.g. cookie expired).
        this.clear()
      }
    },
    persist(token: string) {
      this.token = token
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ token }))
    },
    clear() {
      this.invalidate()
      // Recorded failures belong to the session that just ended.
      this.clearErrors()
    },
    /**
     * Drops a session the API refused to authenticate, keeping recorded failures
     * so the UI can still explain why the user was signed out.
     */
    invalidate() {
      this.token = null
      localStorage.removeItem(STORAGE_KEY)
    },
    /** Notes that a request was handed to the API client; pair with `endRequest`. */
    beginRequest() {
      this.pendingRequests += 1
    },
    /** Notes that a request settled, never dropping below zero. */
    endRequest() {
      this.pendingRequests = Math.max(0, this.pendingRequests - 1)
    },
    /** Keeps a failed response around, e.g. to report it to the user. */
    recordError(error: ApiError) {
      this.errors.push(error)
    },
    clearErrors() {
      this.errors = []
    },
    toast(toast: Omit<Toast, 'key'>): number {
      const toastId = this.runningToastCount
      // increment counter to keep track of unique "IDs" for toasts
      this.runningToastCount += 1
      // add toast to store; the key is assigned here so callers can omit it
      this.toasts[toastId] = { ...toast, key: toastId }
      // set up timer to automatically remove toast after hangtime
      setTimeout(() => this.removeToast(toastId), TOAST_HANGTIME)
      return this.runningToastCount
    },
    removeToast(key: number) {
      delete this.toasts[key]
    },
  },
})
/**
 * Resolves the session store on first use, so plugins can be installed
 * independently of when Pinia becomes active.
 */
export function lazySessionStore(): () => ReturnType<typeof useSessionStore> {
  let store: ReturnType<typeof useSessionStore> | undefined
  return () => (store ??= useSessionStore())
}
