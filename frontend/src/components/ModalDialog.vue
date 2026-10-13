<script setup lang="ts">
import { nextTick, ref, useId, watch } from 'vue';

const props = withDefaults(
  defineProps<{
    /** Text shown in the header; assistive tech reads it as the dialog's label. */
    title: string;
    /** Bootstrap dialog size: omit for the default width, or `sm`, `lg`, `xl`. */
    size?: 'sm' | 'lg' | 'xl';
    /** Accessible name and visible text of the close controls. */
    closeLabel?: string;
  }>(),
  { size: undefined, closeLabel: 'Close' },
);

/** Whether the dialog is open; keep it in sync with the parent through `v-model`. */
const open = defineModel<boolean>({ required: true });

const dialog = ref<HTMLElement | null>(null);
// Generate the id so several dialogs on one page never clash over `aria-labelledby`.
const titleId = useId();

// Move focus into the dialog when it opens, so Escape and the tab order stay with it.
watch(open, async (isOpen) => {
  if (!isOpen) return;
  await nextTick();
  dialog.value?.focus();
});

function close() {
  open.value = false;
}
</script>

<template>
  <div
    v-if="open"
    ref="dialog"
    class="modal fade show d-block"
    tabindex="-1"
    role="dialog"
    aria-modal="true"
    :aria-labelledby="titleId"
    @click.self="close"
    @keydown.esc="close">
    <div class="modal-dialog modal-dialog-centered" :class="props.size && `modal-${props.size}`">
      <div class="modal-content">
        <div class="modal-header">
          <h5 :id="titleId" class="modal-title">{{ props.title }}</h5>
          <button type="button" class="btn-close" :aria-label="props.closeLabel" @click="close" />
        </div>
        <div class="modal-body">
          <slot />
        </div>
        <div class="modal-footer">
          <slot name="footer">
            <button type="button" class="btn btn-secondary" @click="close">
              {{ props.closeLabel }}
            </button>
          </slot>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Bootstrap paints its backdrop with JavaScript, which this app does not load,
   so the dialog shades the page behind it itself. */
.modal {
  background: rgba(0, 0, 0, 0.5);
}
</style>
