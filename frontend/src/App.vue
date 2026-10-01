<script setup lang="ts">
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { useSessionStore } from '@/stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

async function logout() {
  await session.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <header v-if="route.name !== 'login'">
    <nav class="container">
      <ul>
        <li><strong>Academic API Importer</strong></li>
        <li><RouterLink :to="{ name: 'queries' }">Queries</RouterLink></li>
        <li><RouterLink :to="{ name: 'tasks' }">Tasks</RouterLink></li>
        <li><RouterLink :to="{ name: 'tokens' }">Tokens</RouterLink></li>
      </ul>
      <ul>
        <li>
          <button type="button" class="secondary outline" @click="logout">Log out</button>
        </li>
      </ul>
    </nav>
  </header>
  <main class="container">
    <RouterView />
  </main>
</template>

<style scoped>
/* Highlight the active route; Vue Router adds aria-current="page" automatically. */
nav a[aria-current='page'] {
  font-weight: 600;
}
</style>
