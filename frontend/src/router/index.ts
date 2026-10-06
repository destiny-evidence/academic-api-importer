import { createRouter, createWebHistory } from 'vue-router';

import LoginView from '@/views/LoginView.vue';
import ListQueriesView from '@/views/Queries/ListQueriesView.vue';

import TasksView from '@/views/TasksView.vue';
import TokensView from '@/views/TokensView.vue';
import { useSessionStore } from '@/stores/store-session';
import UsersView from '@/views/UsersView.vue';
import ProfileView from '@/views/ProfileView.vue';
import QueryUtilsView from '@/views/Queries/QueryUtilsView.vue';

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
      redirect: { name: 'query-list' },
      children: [
        {
          // Default child: `/queries` shows the query list.
          path: '',
          name: 'query-list',
          component: ListQueriesView,
        },
        {
          path: 'format',
          name: 'query-utils',
          component: QueryUtilsView,
        },
      ],
    },
    {
      path: '/tasks/:query_id?',
      name: 'tasks',
      component: TasksView,
    },
    {
      path: '/users',
      name: 'users',
      component: UsersView,
    },
    {
      path: '/tokens',
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
      path: '/profile',
      name: 'profile',
      component: ProfileView,
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
