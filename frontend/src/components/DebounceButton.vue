<script setup lang="ts">
import { ref } from 'vue';

const props = defineProps({
  onClick: { type: Function, required: true },
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

async function click() {
  try {
    state.value = IconState.Loading;
    await props.onClick();
    state.value = IconState.Success;
  } catch {
    state.value = IconState.Failed;
  } finally {
    setTimeout(() => (state.value = IconState.Ready), props.timeout);
  }
}
</script>

<template>
  <button :disabled="state !== IconState.Ready" @click="click">
    <slot />
  </button>
</template>

<style scoped></style>
