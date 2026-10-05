<script setup lang="ts">
import { computed, ref } from 'vue';
import { BButton, BFormSelect } from 'bootstrap-vue-next/components';
import { BIconPlay, BIconCode } from 'bootstrap-icons-vue';

import CodeTextarea from '@/components/CodeTextarea.vue';
import { Queries, Wrapper } from '@/plugins/api/spec';
import { rejectMessage } from '@/plugins/api';
import { useSessionStore } from '@/stores/session';

const session = useSessionStore();

/**
 * The draft query lives in the session store; `draftQuery` and `wrapper` write
 * it back through `setQueryInfo`, which keeps the localStorage backup current,
 * so the text survives a closed tab.
 */
const draftQuery = computed({
  get: () => session.queryInfo.query,
  set: (query) => session.setQueryInfo({ ...session.queryInfo, query }),
});
const wrappers = Object.values(Wrapper);
const wrapper = computed({
  get: () => session.queryInfo.wrapper,
  set: (value: Wrapper) => session.setQueryInfo({ ...session.queryInfo, wrapper: value }),
});
const feedback = ref<string | null>(null);
const renderedQuery = ref<string | null>(null);
const busy = ref(false);

function renderQuery() {
  busy.value = true;
  Queries.renderQueryQueriesRenderPost({ queryInfo: session.queryInfo })
    .then((response) => {
      feedback.value = null;
      renderedQuery.value = response.data ?? null;
    })
    .catch((reason) => {
      renderedQuery.value = null;
      feedback.value = rejectMessage(reason);
    })
    .finally(() => {
      busy.value = false;
    });
}
</script>

<template>
  <section>
    <h1>Test query</h1>
    <div class="mb-3">
      <div class="d-flex justify-content-between align-items-end gap-2 mb-1">
        <label class="form-label small text-body-secondary mb-0" for="test-query-input">
          Query
        </label>
        <div class="d-flex align-items-center gap-2">
          <label class="small text-body-secondary mb-0" for="test-query-wrapper">Wrapper</label>
          <BFormSelect
            id="test-query-wrapper"
            v-model="wrapper"
            size="sm"
            class="w-auto font-monospace">
            <option v-for="option in wrappers" :key="option" :value="option">{{ option }}</option>
          </BFormSelect>
        </div>
      </div>

      <CodeTextarea
        id="test-query-input"
        v-model="draftQuery"
        :rows="8"
        autofocus
        placeholder="Enter a query to send to the academic API…" />
    </div>
    <!-- TODO link to https://www.elsevier.support/scopus/answer/how-can-i-best-use-the-advanced-search#tips -->

    <BButton variant="primary" :busy="busy" @click="renderQuery">
      <BIconCode />
      Format
    </BButton>
    <BButton variant="primary" :busy="busy" class="ms-2" disabled>
      <BIconPlay />
      Test
    </BButton>

    <div v-if="renderedQuery" class="mt-4">
      <h2 class="h5">Rendered query</h2>
      <CodeTextarea :model-value="renderedQuery" readonly :rows="12" />
    </div>

    <template v-if="feedback">
      <h2 class="h5 mt-4">Feedback</h2>
      <pre class="bg-body-tertiary border rounded p-3 small">{{ feedback }}</pre>
    </template>
  </section>
</template>

<style scoped></style>
