import { defineStore } from 'pinia'

const STORAGE_KEY = 'aai-session'

/** The access token persisted in localStorage, if any. */
function loadStoredToken(): string | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw).token ?? null) : null
  } catch {
    return null
  }
}

interface LoginResponse {
  access_token?: string
  token_type?: string
}

interface ApiError {
  detail?: string
}

/**
 * Session state backed by localStorage.
 *
 * Login is handled by `POST /users/login`, which validates credentials and sets
 * the `aai_user_token` cookie itself. That cookie is HttpOnly, so it cannot be
 * read from JavaScript; the session is tracked from the returned access token
 * and sent to the API as a bearer token (the cookie also travels with
 * credentialed same-origin requests).
 */
export const useSessionStore = defineStore('session', {
  state: () => ({
    token: loadStoredToken(),
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.token),
  },
  actions: {
    /** Authenticate with email and password; persists the returned access token. */
    async login(email: string, password: string) {
      const response = await fetch('/users/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email, password }),
      })
      if (!response.ok) {
        let detail = ''
        try {
          detail = ((await response.json()) as ApiError)?.detail ?? ''
        } catch {
          /* non-JSON error body */
        }
        throw new Error(detail || `Login failed (${response.status})`)
      }

      // Token response: { access_token, token_type: "bearer" }
      const data = (await response.json()) as LoginResponse
      this.persist(data?.access_token ?? '')
    },
    /** Ask the backend to clear the auth cookie, then drop the local session. */
    async logout() {
      try {
        await fetch('/users/logout', { method: 'POST', credentials: 'include' })
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
      this.token = null
      localStorage.removeItem(STORAGE_KEY)
    },
  },
})
