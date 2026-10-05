<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';

/**
 * Monospace textarea with a line-number gutter and a status footer showing the
 * caret position and total length, for editing academic API queries.
 *
 * Soft wrapping is disabled (`wrap="off"`, `white-space: pre`) so each logical
 * line occupies exactly one visual row and the gutter numbers stay aligned;
 * long lines scroll horizontally, and the gutter mirrors the vertical scroll.
 */
defineOptions({ inheritAttrs: false });

const props = withDefaults(
  defineProps<{
    modelValue: string;
    id?: string;
    rows?: number;
    readonly?: boolean;
    placeholder?: string;
  }>(),
  { id: undefined, rows: 8, readonly: false, placeholder: undefined },
);

const emit = defineEmits<{ 'update:modelValue': [value: string] }>();

const textareaEl = ref<HTMLTextAreaElement | null>(null);
const gutterEl = ref<HTMLElement | null>(null);
/** Selection start of the textarea, mirrored from its events. */
const caret = ref(0);

/** One entry per logical line of the value, for the gutter. */
const lineNumbers = computed(() =>
  Array.from({ length: props.modelValue.split('\n').length }, (_, index) => index + 1),
);

const caretLine = computed(() => {
  const beforeCaret = props.modelValue.slice(0, Math.min(caret.value, props.modelValue.length));
  return beforeCaret.split('\n').length;
});
/** 1-based column of the caret within its line. */
const caretColumn = computed(() => {
  const beforeCaret = props.modelValue.slice(0, Math.min(caret.value, props.modelValue.length));
  return beforeCaret.length - beforeCaret.lastIndexOf('\n');
});
const characterCount = computed(() => props.modelValue.length);

function updateCaret() {
  caret.value = textareaEl.value?.selectionStart ?? 0;
}

function onInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value);
  updateCaret();
}

function syncGutter() {
  if (gutterEl.value && textareaEl.value) gutterEl.value.scrollTop = textareaEl.value.scrollTop;
}

// Programmatic value changes (e.g. the rendered query arriving) move the caret;
// the textarea has the new value after the update flush.
watch(
  () => props.modelValue,
  async () => {
    await nextTick();
    updateCaret();
  },
);
</script>

<template>
  <div class="border rounded overflow-hidden bg-body">
    <div class="d-flex align-items-stretch">
      <div
        ref="gutterEl"
        class="gutter text-end text-body-secondary bg-body-tertiary user-select-none overflow-hidden"
        aria-hidden="true">
        <div v-for="line in lineNumbers" :key="line">{{ line }}</div>
      </div>
      <textarea
        :id="id"
        ref="textareaEl"
        class="editor form-control form-control-sm border-0 rounded-0 font-monospace flex-grow-1 shadow-none"
        wrap="off"
        spellcheck="false"
        :value="modelValue"
        :rows="rows"
        :readonly="readonly"
        :placeholder="placeholder"
        v-bind="$attrs"
        @input="onInput"
        @scroll="syncGutter"
        @focus="updateCaret"
        @click="updateCaret"
        @keyup="updateCaret"
        @select="updateCaret" />
    </div>
    <div
      class="d-flex justify-content-between small text-body-secondary border-top bg-body-tertiary px-2 py-1">
      <span>Ln {{ caretLine }}, Col {{ caretColumn }}</span>
      <span>{{ characterCount }} characters</span>
    </div>
  </div>
</template>

<style scoped>
/* The gutter numbers must match the textarea's font metrics exactly; `pre`
   (instead of the textarea default `pre-wrap`) keeps one row per logical line. */
.gutter,
.editor {
  font-family: var(--bs-font-monospace);
  font-size: 0.875rem;
  line-height: 1.5;
  white-space: pre;
}

.gutter {
  /* Vertical padding must equal `form-control-sm`'s 0.25rem for row alignment. */
  padding: 0.25rem 0.5rem;
  min-width: 2.25rem;
}

.editor {
  /* Fixed box: the text scrolls inside and the gutter follows via @scroll. */
  resize: none;
  overflow-x: auto;
}
</style>
