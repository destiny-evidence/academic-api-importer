import type { ApiError } from '@/stores/session';

import { type ApiClientPlugin } from './plugin';
import { lazySessionStore } from '@/stores/session';

/**
 * Counts requests in flight in the session store and collects failed ones.
 *
 * `session.pendingRequests` goes up when a request is sent and down again when it
 * settles, be it with a response or an error. Errors additionally land in
 * `session.errors`, where the response payload is available as `body`.
 *
 * Note that only requests made through the generated client are tracked; a
 * hand-written `fetch()` call is invisible to this plugin.
 */
export function createRequestCounterPlugin(): ApiClientPlugin {
  const session = lazySessionStore();

  // The client runs response interceptors before it looks at the status code, so a
  // failing request passes through the response *and* the error interceptor.
  // Remembering counted requests keeps the counter balanced.
  const inFlight = new WeakSet<Request>();

  const settle = (request: Request | undefined) => {
    if (request && inFlight.delete(request)) session().endRequest();
  };

  return {
    install(client) {
      client.interceptors.request.use((request) => {
        if (!inFlight.has(request)) {
          inFlight.add(request);
          session().beginRequest();
        }
        return request;
      });

      client.interceptors.response.use((response, request) => {
        settle(request);
        return response;
      });

      client.interceptors.error.use((error, response, request) => {
        settle(request);
        const failure: ApiError = {
          at: new Date().toISOString(),
          // The client throws the parsed response body for error responses, and the
          // original error for requests that never made it to the API.
          body: error,
          method: request?.method ?? '',
          status: response?.status ?? 0,
          statusText: response?.statusText ?? '',
          url: response?.url || request?.url || '',
        };
        session().recordError(failure);
        return error;
      });
    },
  };
}
