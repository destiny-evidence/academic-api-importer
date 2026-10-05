<script setup lang="ts">
import { ref } from 'vue';
import { BButton, BFormTextarea } from 'bootstrap-vue-next/components';
import { BIconPlay } from 'bootstrap-icons-vue';

import { Queries, type QueryInfo } from '@/plugins/api/spec';
import { rejectMessage } from '@/plugins/api';

const queryInfo = ref<QueryInfo>({
  query: '',
  wrapper: 'scopus',
});
const feedback = ref<string | null>(null);
const renderedQuery = ref<string | null>(null);
const busy = ref(false);

function renderQuery() {
  busy.value = true;
  Queries.renderQueryQueriesRenderPost({ queryInfo: queryInfo.value })
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
      <label class="form-label small text-body-secondary" for="test-query-input">Query</label>
      <BFormTextarea
        id="test-query-input"
        v-model="queryInfo.query"
        rows="8"
        max-rows="16"
        autofocus
        placeholder="Enter a query to send to the academic API…" />
    </div>

    <BButton variant="primary" :busy="busy" @click="renderQuery">
      <BIconPlay />
      Run
    </BButton>

    <div v-if="renderedQuery" class="mt-4">
      <h2 class="h5">Rendered query</h2>
      <BFormTextarea
        :model-value="renderedQuery"
        readonly
        rows="12"
        max-rows="24"
        class="font-monospace" />
    </div>

    <template v-if="feedback">
      <h2 class="h5 mt-4">Feedback</h2>
      <pre class="bg-body-tertiary border rounded p-3 small">{{ feedback }}</pre>
    </template>
  </section>
</template>

<style scoped></style>
