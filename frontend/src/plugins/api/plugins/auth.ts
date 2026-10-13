import { type ApiClientPlugin } from './plugin';

import { meta, session } from '@/stores';

/** Logging in proves nothing but the given credentials, so no session travels. */
const LOGIN_PATH = '/users/login';

/**
 * Sends the session's access token as a bearer token, and has the meta store drop
 * the session (and everything built on it) when the API refuses to accept it.
 *
 * Requests that carry their own `Authorization` header are left alone, as is
 * logging in: a wrong password must not look like a rejected session.
 */
export function createAuthPlugin(): ApiClientPlugin {
  const authenticate = (request: Request) =>
    // A token of their own wins, and so does an endpoint that is not ours to sign.
    !request.headers.has('Authorization') && !request.url.endsWith(LOGIN_PATH);

  return {
    install(client) {
      client.interceptors.request.use((request) => {
        const { token } = session();
        if (token && authenticate(request)) {
          request.headers.set('Authorization', `Bearer ${token}`);
        }
        return request;
      });

      client.interceptors.error.use((error, response, request) => {
        // A 401 for a request that carried our token means the session expired or
        // was revoked; without one, the API rejected something else. Everything
        // built on those credentials goes with them, hence the meta store.
        if (response?.status === 401 && request?.headers.has('Authorization')) {
          meta().invalidate();
        }
        return error;
      });
    },
  };
}
