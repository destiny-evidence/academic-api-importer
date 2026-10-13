import { defineStore } from 'pinia';

import { registerSubStore } from './registry';

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

/**
 * What the app is doing right now: notifications to show, and API requests in
 * flight or already failed. Deliberately not persisted; a reload starts with a
 * clean slate, and a page that is no longer open has nothing left to report
 * about or wait for.
 */
export const useActivityStore = defineStore('activity', {
  state: () => ({
    /** Requests sent by the API client that have not settled yet. */
    pendingRequests: 0,
    /** Requests that failed, oldest first. */
    errors: [] as ApiError[],
    toasts: {} as Record<number, Toast>,
    runningToastCount: 0 as number, // used to generate unique "IDs" for toasts
  }),
  getters: {
    isBusy: (state) => state.pendingRequests > 0,
  },
  actions: {
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
    /**
     * The app session these requests were made for is over, and the failures
     * recorded for it are history with it. Toasts are the one thing kept: they
     * are usually the message saying the session ended, so they expire on their
     * own. There is no `invalidate()` to go with it - the failures a refused
     * session leaves behind are what explains the sign-out to the user.
     */
    clear() {
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

registerSubStore(useActivityStore);
