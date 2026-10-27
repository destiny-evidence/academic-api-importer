<script setup lang="ts">
import { ref } from 'vue';
import { BIconArrowClockwise, BIconCheckCircle, BIconExclamationCircle } from 'bootstrap-icons-vue';

const props = defineProps({
  refresh: { type: Function, required: true },
  timeout: { type: Number, required: false, default: 1000 },
});

enum IconState {
  Ready,
  Loading,
  Failed,
  Success,
}

// Refresh icon status
const state = ref<IconState>(IconState.Ready);

async function reload() {
  try {
    state.value = IconState.Loading;
    await props.refresh();
    state.value = IconState.Success;
  } catch {
    state.value = IconState.Failed;
  } finally {
    setTimeout(() => (state.value = IconState.Ready), props.timeout);
  }
}
</script>

<template>
  <span>
    <span
      v-if="state == IconState.Loading"
      class="spinner-border spinner-border-sm text-secondary"
      role="status">
      <span class="visually-hidden">Loading...</span>
    </span>

    <span
      v-else-if="state == IconState.Ready"
      class="clickable-icon"
      role="button"
      tabindex="0"
      @click="reload"
      @keyup.enter="reload"
      @keyup.space="reload">
      <BIconArrowClockwise />
    </span>

    <span v-else-if="state == IconState.Success" class="text-success">
      <BIconCheckCircle />
    </span>

    <span v-else-if="state == IconState.Failed" class="text-danger">
      <BIconExclamationCircle />
    </span>
  </span>
</template>
