import 'bootstrap/dist/css/bootstrap.min.css';
// bootstrap-vue-next builds on Bootstrap but adds its own pieces, e.g. the sort
// icons and busy styling the tables rely on.
import 'bootstrap-vue-next/dist/bootstrap-vue-next.css';

import { createPinia } from 'pinia';
import { createApp } from 'vue';

import {
  createRequestCounterPlugin,
  createAuthPlugin,
  installApiClientPlugins,
} from '@/plugins/api/plugins';
import { logReject } from '@/plugins/api';
import { client } from '@/plugins/api/spec';
import { useSessionStore } from '@/stores/store-session';

import App from './App.vue';
import router from './router';

const app = createApp(App);

app.use(createPinia());
app.use(router);

if (AAPI_VITE_API_URL) client.setConfig({ baseUrl: AAPI_VITE_API_URL });

// Authenticate API requests with the session token and count running requests in
// the activity store (both require an active Pinia).
installApiClientPlugins(client, [createAuthPlugin(), createRequestCounterPlugin()]);

// Re-sync a session read back from storage: fetch a profile it lost, or drop an
// auth cookie that outlived its session. Must run after the plugins above. The
// router guard only reads `token`, which the store restores synchronously from
// localStorage, so this may settle while the app boots.
useSessionStore().restore().catch(logReject);

app.mount('#app');
