<script setup lang="ts">
import { BIconCheck, BIconCopy } from 'bootstrap-icons-vue';
import { ref } from 'vue';

const props = defineProps<{
  value: string;
}>();

const copied = ref(false);

async function copyToClipboard() {
  try {
    await navigator.clipboard.writeText(props.value);
    copied.value = true;
    setTimeout(() => {
      copied.value = false;
    }, 2000);
  } catch (err) {
    console.error('Failed to copy text: ', err);
  }
}
</script>

<template>
  <div class="code-container">
    <pre>{{ props.value }}</pre>
    <button
      class="copy-btn"
      @click="copyToClipboard"
      :title="copied ? 'Copied!' : 'Copy to clipboard'"
      :aria-label="copied ? 'Copied' : 'Copy to clipboard'">
      <!-- Success Check Icon -->
      <BIconCheck v-if="copied" class="icon success" />
      <!-- Copy Icon -->
      <BIconCopy v-else />
    </button>
  </div>
</template>

<style scoped>
.code-container {
  position: relative;
  display: inline-block;
  max-width: 100%;
}

pre {
  background-color: #f4f4f5;
  color: #18181b;
  padding: 0.1em 2em 0.1em 0.3em;
  border-radius: 0.375rem;
  font-family: monospace;
  margin: 0;
  /* A scroll container reports its bottom edge as its baseline, which lifts the
     chip above the text it sits in (bootstrap's reboot sets `pre { overflow:
     auto }`, so this has to be overridden, not just left unset). Long values
     wrap instead of scrolling. */
  overflow: visible;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.copy-btn {
  position: absolute;
  top: 0.1em;
  right: 0.1em;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0.1em;
  background: transparent;
  border: none;
  cursor: pointer;
  color: #71717a;
}

.icon.success {
  color: #16a34a;
}
</style>
