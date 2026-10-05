<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { BTable } from 'bootstrap-vue-next/components';
import type { TableField } from 'bootstrap-vue-next/types';

import ModalDialog from '@/components/ModalDialog.vue';
import { toastReject } from '@/plugins/api';
import { Queries, type Query } from '@/plugins/api/spec';
import { formatTimeStamp } from '@/utils';

/** Longer queries are shortened to this many characters in the table. */
const PREVIEW_LENGTH = 10;

const router = useRouter();
const queries = ref<Query[]>([]);
const loading = ref(true);
/** Full text of the query shown in the modal; `null` while the modal is closed. */
const fullQuery = ref<string | null>(null);

// The modal tracks its open state through `v-model`; closing it clears the text
// so `fullQuery === null` remains the one truth about whether it is closed.
const fullQueryOpen = computed({
  get: () => fullQuery.value !== null,
  set: (isOpen) => {
    if (!isOpen) fullQuery.value = null;
  },
});

// Keys double as `Query` property names, so the table sorts the raw values
// even where a slot or formatter changes how a cell is displayed.
const columns: TableField<Query>[] = [
  { key: 'query_id', label: 'ID', sortable: true, tdClass: 'fw-bold' },
  { key: 'query', label: 'Query', sortable: true },
  { key: 'wrapper', label: 'Wrapper', sortable: true },
  {
    key: 'created_at',
    label: 'Created At',
    sortable: true,
    formatter: ({ value }) => formatTimeStamp(value as Query['created_at']),
    tdClass: 'text-muted small',
  },
];

onMounted(async () => {
  try {
    const { data } = await Queries.listQueriesQueriesGet({}, { credentials: 'include' });
    queries.value = data ?? [];
  } catch (reason) {
    toastReject(reason);
  } finally {
    loading.value = false;
  }
});

/** The table only shows the start of long queries; the modal shows all of them. */
function preview(query: string): string {
  return query.length > PREVIEW_LENGTH ? `${query.slice(0, PREVIEW_LENGTH)}...` : query;
}

function isTruncated(query: string): boolean {
  return query.length > PREVIEW_LENGTH;
}

/** A row leads to another view, so only rows that actually lead somewhere invite a click. */
function rowClass(item: Query | null): string {
  return item?.query_id != null ? 'is-link' : '';
}

/** A row leads to the tokens a query can be run with, named by its ID. */
function openTokens(query: Query) {
  if (query.query_id == null) return;
  router.push({ name: 'tokens', params: { query_id: query.query_id } });
}
</script>

<template>
  <section>
    <h1 class="mb-4">Queries</h1>
    <BTable
      :items="queries"
      :fields="columns"
      :busy="loading"
      :tbody-tr-class="rowClass"
      head-variant="light"
      hover
      responsive
      show-empty
      sort-icon-left
      empty-text="No queries yet."
      @row-clicked="openTokens($event.item)">
      <template #table-busy>
        <td :colspan="columns.length" class="text-body-secondary">Loading queries…</td>
      </template>
      <template #cell(query)="{ item }">
        {{ preview(item.query) }}
        <span
          v-if="isTruncated(item.query)"
          class="show-full small text-muted ms-1"
          role="button"
          tabindex="0"
          @click.stop="fullQuery = item.query"
          @keyup.enter.stop="fullQuery = item.query">
          [show full]
        </span>
      </template>
      <template #cell(wrapper)="{ item }">
        <code>{{ item.wrapper }}</code>
      </template>
    </BTable>

    <!-- Full query modal -->
    <ModalDialog v-model="fullQueryOpen" title="Full Query" size="lg">
      <pre class="full-query bg-light p-3 border rounded">{{ fullQuery }}</pre>
    </ModalDialog>
  </section>
</template>

<style scoped>
/* Rows lead to another view, so they invite a click. The rows belong to
   BTable, so the selector has to reach into it. */
:deep(tbody tr.is-link) {
  cursor: pointer;
}

.show-full {
  cursor: pointer;
}

.full-query {
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
