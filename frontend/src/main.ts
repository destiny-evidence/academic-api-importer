import 'bootstrap/dist/css/bootstrap.min.css'

import { createPinia } from 'pinia'
import { createApp } from 'vue'

import { createAuthPlugin } from '@/plugins/api/auth'
import { installApiClientPlugins } from '@/plugins/api/plugin'
import { createRequestCounterPlugin } from '@/plugins/api/request-counter'
import { client } from '@/plugins/api/spec'

import App from './App.vue'
import router from './router'

const app = createApp(App)

app.use(createPinia())
app.use(router)

// Authenticate API requests with the session token and count running requests in
// the session store (both require an active Pinia).
installApiClientPlugins(client, [createAuthPlugin(), createRequestCounterPlugin()])

app.mount('#app')
