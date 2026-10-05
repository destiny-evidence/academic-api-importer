<script setup lang="ts">
import { useSessionStore } from '@/stores/session';

const session = useSessionStore();
</script>

<template>
  <div
    role="alert"
    class="toast-container position-fixed top-0 end-0 p-3 pt-5 mh-100 overflow-auto"
    aria-live="assertive"
    aria-atomic="true">
    <div
      v-for="toast in session.toasts"
      :key="toast.key"
      role="alert"
      class="toast align-items-center border-0 show mb-2"
      :class="[toast.bg, toast.col]"
      aria-live="assertive"
      aria-atomic="true">
      <div class="d-flex">
        <div class="toast-body text-start" v-html="toast.htmlMessage" />
        <button
          type="button"
          class="btn-close btn-close-white me-2 m-auto"
          aria-label="Close"
          @click="session.removeToast(toast.key)" />
      </div>
    </div>
  </div>
</template>
