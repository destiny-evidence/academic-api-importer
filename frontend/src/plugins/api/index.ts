import { type ToastType, lazySessionStore, LevelStyle } from '@/stores/session';
import { escapeHtml } from '@/utils';

/**
 * Success handlers and error handlers for the promises returned by the
 * generated API client (`@hey-api/openapi-ts`). The client is configured with
 * `throwOnError`, so a request resolves with `{data, request, response}` when
 * the API accepts it and rejects with the parsed error body (FastAPI's
 * `{detail: '...'}`) or the thrown error (network failures) when it does not.
 *
 * Handlers pass their input through, so they can be chained:
 *
 * ```ts
 * Users.loginUsersLoginPost({ body }).then(toastSuccess).catch(toastReject)
 * Tokens.addTokenTokensSubmitPost({ body })
 *   .then(toastSuccessWith('Token added'))
 *   .catch(toastReject)
 * ```
 */

/** What a successful request resolves with (`responseStyle: 'fields'`). */
export interface ApiSuccess {
  data: unknown;
  request: Request;
  response: Response;
}

const session = lazySessionStore();

/** Publishes a toast through the session store, where `ToastsViewer` picks it up. */
export function postToast(level: ToastType, message: string) {
  session().toast({
    level,
    message,
    htmlMessage: escapeHtml(message),
    ...LevelStyle[level],
  });
}

/** Human-readable text of a rejected request's error payload. */
export function rejectMessage(reason: unknown): string {
  if (typeof reason === 'string') return reason;
  if (reason instanceof Error) return reason.message;
  if (reason && typeof reason === 'object') {
    const { detail } = reason as { detail?: unknown };
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      // Validation errors arrive as a list of problems, e.g. [{loc, msg, type}]
      const problems = detail
        .map((problem) => (problem as { msg?: unknown })?.msg)
        .filter((msg): msg is string => typeof msg === 'string');
      if (problems.length) return problems.join('; ');
    }
  }
  return 'Request failed';
}

/** Does nothing; handy as `.catch(ignore)` when failures need no reporting. */
export function ignore() {}

/** Logs a rejected request to the console. */
export function logReject(reason: unknown) {
  console.error(reason);
}

/**
 * Toast handler for rejections of the client's promises. Reports the API's
 * error detail, and swallows the rejection so it does not surface as an
 * unhandled error.
 */
export function toastReject(reason: unknown) {
  postToast('ERROR', rejectMessage(reason));
}

/**
 * Toast handler for the client's successful responses, reporting the response
 * status. The result passes through unchanged (and keeps its type), so this
 * can be installed via `.then(toastSuccess)`.
 */
export function toastSuccess<T extends ApiSuccess>(result: T): T {
  const { status, statusText } = result.response;
  postToast('SUCCESS', `${status} ${statusText}`.trim());
  return result;
}

/**
 * Like `toastSuccess`, but reports the given message instead of the response
 * status: `.then(toastSuccessWith('Token added'))`.
 */
export function toastSuccessWith(message: string): <T extends ApiSuccess>(result: T) => T {
  return <T extends ApiSuccess>(result: T): T => {
    postToast('SUCCESS', message);
    return result;
  };
}
