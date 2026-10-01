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
import { client } from '@/plugins/api/spec';

import App from './App.vue';
import router from './router';

const app = createApp(App);

app.use(createPinia());
app.use(router);

// Authenticate API requests with the session token and count running requests in
// the session store (both require an active Pinia).
installApiClientPlugins(client, [createAuthPlugin(), createRequestCounterPlugin()]);

app.mount('#app');
