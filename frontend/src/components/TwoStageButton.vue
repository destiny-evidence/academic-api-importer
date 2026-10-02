<template>
  <div>
    <button
      type="button"
      class="btn"
      :class="btnStyle"
      @click="confirming = true"
      :disabled="disabled || confirming">
      <slot />
    </button>
    <template v-if="confirming"
      ><span
        class="ms-2 me-2"
        role="button"
        tabindex="0"
        @click="accept"
        @keyup.enter="accept"
        @keyup.space="accept"
        >Yes</span
      >
      <span
        class="me-2"
        role="button"
        tabindex="0"
        @click="reject"
        @keyup.enter="reject"
        @keyup.space="reject"
        >No</span
      ></template
    >
  </div>
</template>
<script lang="ts">
import { defineComponent } from 'vue';

export default defineComponent({
  name: 'ConfirmationButton',
  emits: ['cancelled', 'accepted'],
  props: {
    disabled: { type: Boolean, default: false, required: false },
    btnStyle: { type: String, default: 'btn-outline-primary', required: false },
  },
  data() {
    return { confirming: false };
  },
  methods: {
    accept() {
      this.confirming = false;
      this.$emit('accepted');
    },
    reject() {
      this.confirming = false;
      this.$emit('cancelled');
    },
  },
});
</script>
