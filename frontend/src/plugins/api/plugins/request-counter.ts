import { activity } from '@/stores';
import type { ApiError } from '@/stores/store-activity';

import { type ApiClientPlugin } from './plugin';

/**
 * Counts requests in flight in the activity store and collects failed ones.
 *
 * `activity.pendingRequests` goes up when a request is sent and down again when it
 * settles, be it with a response or an error. Errors additionally land in
 * `activity.errors`, where the response payload is available as `body`.
 *
 * Note that only requests made through the generated client are tracked; a
 * hand-written `fetch()` call is invisible to this plugin.
 */
export function createRequestCounterPlugin(): ApiClientPlugin {
  // The client runs response interceptors before it looks at the status code, so a
  // failing request passes through the response *and* the error interceptor.
  // Remembering counted requests keeps the counter balanced.
  const inFlight = new WeakSet<Request>();

  const settle = (request: Request | undefined) => {
    if (request && inFlight.delete(request)) activity().endRequest();
  };

  return {
    install(client) {
      client.interceptors.request.use((request) => {
        if (!inFlight.has(request)) {
          inFlight.add(request);
          activity().beginRequest();
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
        activity().recordError(failure);
        return error;
      });
    },
  };
}
