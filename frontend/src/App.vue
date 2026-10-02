<script setup lang="ts">
import { computed } from 'vue';
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router';

import ToastsViewer from '@/components/ToastsViewer.vue';
import { useSessionStore } from '@/stores/session';

const route = useRoute();
const router = useRouter();
const session = useSessionStore();

async function logout() {
  await session.logout();
  router.push({ name: 'login' });
}

const requestInProgress = computed(() => session.isBusy);
</script>

<template>
  <nav v-if="route.name !== 'login'" class="navbar navbar-expand-sm bg-body-tertiary border-bottom">
    <div class="container">
      <RouterLink class="navbar-brand" :to="{ name: 'queries' }">
        Academic API Importer
        <!-- The spinner always occupies its box; `invisible` (visibility: hidden)
             acts as the equally sized placeholder so the brand never shifts, and
             hides the status from assistive tech while idle. -->
        <span
          class="spinner-border spinner-border-sm ms-2 align-middle"
          :class="{ invisible: !requestInProgress }"
          role="status">
          <span class="visually-hidden">Requests in progress…</span>
        </span>
      </RouterLink>
      <div class="navbar-nav me-auto">
        <RouterLink class="nav-link" :to="{ name: 'queries' }">Queries</RouterLink>
        <RouterLink class="nav-link" :to="{ name: 'tasks' }">Tasks</RouterLink>
        <RouterLink class="nav-link" :to="{ name: 'tokens' }">Tokens</RouterLink>
        <RouterLink class="nav-link" :to="{ name: 'profile' }">Profile</RouterLink>
        <RouterLink v-if="session.user?.permission_admin" class="nav-link" :to="{ name: 'users' }"
          >Users</RouterLink
        >
      </div>

      <div class="navbar-text me-2">{{ session.user?.full_name }}</div>
      <button type="button" class="btn btn-outline-secondary btn-sm" @click="logout">
        Log out
      </button>
    </div>
  </nav>
  <main class="container py-4">
    <RouterView />
  </main>
  <ToastsViewer />
</template>

<style scoped>
/* Highlight the active route; Vue Router adds aria-current="page" automatically. */
nav a[aria-current='page'] {
  font-weight: 600;
}
</style>
