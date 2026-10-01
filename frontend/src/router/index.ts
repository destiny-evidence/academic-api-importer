import { createRouter, createWebHistory } from 'vue-router';

import LoginView from '@/views/LoginView.vue';
import QueriesView from '@/views/QueriesView.vue';
import TasksView from '@/views/TasksView.vue';
import TokensView from '@/views/TokensView.vue';
import { useSessionStore } from '@/stores/session';
import UsersView from '@/views/UsersView.vue';

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      redirect: { name: 'queries' },
    },
    {
      path: '/queries',
      name: 'queries',
      component: QueriesView,
    },
    {
      path: '/tasks',
      name: 'tasks',
      component: TasksView,
    },
    {
      path: '/users',
      name: 'users',
      component: UsersView,
    },
    {
      // The optional `query_id` lets the queries table ask for the tokens of one
      // particular query; the navbar links here without it.
      path: '/tokens/:query_id?',
      name: 'tokens',
      component: TokensView,
    },
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { requiresAuth: false },
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: { name: 'queries' },
    },
  ],
});

// Any route without an explicit `requiresAuth: false` requires a session.
router.beforeEach((to) => {
  const session = useSessionStore();

  if (to.meta.requiresAuth !== false && !session.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } };
  }
  if (to.name === 'login' && session.isAuthenticated) {
    return { name: 'queries' };
  }
});

export default router;
