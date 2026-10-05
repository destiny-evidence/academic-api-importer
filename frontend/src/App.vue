<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router';
import { BCollapse, BDropdown, BDropdownItem } from 'bootstrap-vue-next/components';

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

// The collapsible menu (navbar-toggler below) and the Queries dropdown are
// built from bootstrap-vue-next components because the Bootstrap JS bundle,
// which normally drives them, is not loaded.
const menuOpen = ref(false);
const queriesActive = computed(() => route.matched.some(({ name }) => name === 'queries'));

// Close the collapsed menu after navigating, mirroring the Bootstrap navbar's
// default behavior (which relies on the JS bundle we do not load).
watch(
  () => route.fullPath,
  () => {
    menuOpen.value = false;
  },
);
</script>

<template>
  <nav v-if="route.name !== 'login'" class="navbar navbar-expand-lg bg-body-tertiary border-bottom">
    <div class="container">
      <RouterLink class="navbar-brand" :to="{ name: 'query-list' }">
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
      <button
        type="button"
        class="navbar-toggler"
        aria-controls="app-nav-menu"
        aria-label="Toggle navigation"
        :aria-expanded="menuOpen ? 'true' : 'false'"
        @click="menuOpen = !menuOpen">
        <span class="navbar-toggler-icon"></span>
      </button>

      <BCollapse id="app-nav-menu" v-model="menuOpen" is-nav>
        <div class="navbar-nav me-auto">
          <BDropdown
            is-nav
            text="Queries"
            variant="link"
            wrapper-class="nav-item"
            :class="{ 'queries-active': queriesActive }">
            <BDropdownItem :to="{ name: 'query-list' }">List queries</BDropdownItem>
            <BDropdownItem :to="{ name: 'query-format' }">Format query</BDropdownItem>
            <BDropdownItem :to="{ name: 'query-test' }">Test query</BDropdownItem>
          </BDropdown>
          <RouterLink class="nav-link" :to="{ name: 'tasks' }">Tasks</RouterLink>
          <RouterLink class="nav-link" :to="{ name: 'tokens' }">Tokens</RouterLink>
          <RouterLink class="nav-link" :to="{ name: 'profile' }">Profile</RouterLink>
          <RouterLink v-if="session.user?.permission_admin" class="nav-link" :to="{ name: 'users' }"
            >Users</RouterLink
          >
        </div>

        <div class="d-flex align-items-lg-center gap-2 mt-2 mt-lg-0">
          <span class="navbar-text me-2">{{ session.user?.full_name }}</span>
          <button type="button" class="btn btn-outline-secondary btn-sm" @click="logout">
            Log out
          </button>
        </div>
      </BCollapse>
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

/* The Queries dropdown toggle is not a router link, so mark it active here
   when one of the query routes is displayed. */
nav :deep(.queries-active .dropdown-toggle) {
  font-weight: 600;
}
</style>
