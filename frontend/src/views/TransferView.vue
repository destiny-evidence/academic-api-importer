<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { BButton, BFormCheckbox, BFormSelect, BTable } from 'bootstrap-vue-next/components';
import { BIconChevronLeft, BIconChevronRight } from 'bootstrap-icons-vue';
import type { TableField } from 'bootstrap-vue-next/types';

import { toastReject } from '@/plugins/api';
import { type SearchIngest, Transfer } from '@/plugins/api/spec';
import { formatNumber, queryParam } from '@/utils';
import { updateQuery } from '@/router';

/** Page sizes offered by the page size selector. */
const PAGE_SIZES = [10, 20, 50, 100];

const route = useRoute();

/** 1-based in the URL ("page=2" is the second page), omitted for the first. */
const page = computed<number>({
  get: () => {
    const value = Number(queryParam(route.query.page));
    return Number.isInteger(value) && value > 1 ? value - 1 : 0;
  },
  set: (value) => updateQuery(route, { page: value > 0 ? String(value + 1) : undefined }),
});

/** Listing only unfinished ingests is kept in the URL like the other filters. */
const onlyOpen = computed<boolean>({
  get: () => queryParam(route.query.only_open) === 'true',
  // Filtering changes what is listed, so it starts over on the first page.
  set: (onlyOpen) =>
    updateQuery(route, { only_open: onlyOpen ? 'true' : undefined, page: undefined }),
});

const ingests = ref<SearchIngest[]>([]);
const loading = ref(true);
const pageLimit = ref<number>(20);

// Keys double as `SearchIngest` property names; the `status` column has no
// item data and is filled entirely by its slot.
const columns: TableField<SearchIngest>[] = [
  { key: 'ingest_id', label: 'Ingest', tdClass: 'text-nowrap' },
  { key: 'query_id', label: 'Query' },
  { key: 'status', label: 'Status' },
  { key: 'checkpoint_bytes', label: 'Checkpoint (bytes)', tdClass: 'text-end text-nowrap' },
  { key: 'checkpoint_rows', label: 'Checkpoint (rows)', tdClass: 'text-end text-nowrap' },
  { key: 'azure_link', label: 'Result file' },
];

/** Where the ingest stands on its way to the ingestion queue. */
function statusOf(ingest: SearchIngest): { text: string; variant: string } {
  if (ingest.is_queued) return { text: 'Queued', variant: 'text-bg-success' };
  if (ingest.is_queueing) return { text: 'Queueing', variant: 'text-bg-warning' };
  return { text: 'Pending', variant: 'text-bg-secondary' };
}

/** Byte count with unit; plain placeholder for ingests without a checkpoint. */
function formatBytes(value?: number | null): string {
  return value == null ? '—' : `${formatNumber(value, 0)} bytes`;
}

function loadIngests() {
  loading.value = true;
  Transfer.listIngestsIngestGet({
    offset: pageLimit.value * page.value,
    limit: pageLimit.value,
    only_open: onlyOpen.value || undefined,
  })
    .then(({ data }) => {
      console.log(data);
      ingests.value = data ?? [];
    })
    .catch(toastReject)
    .finally(() => {
      loading.value = false;
    });
}

watch([page, pageLimit, onlyOpen], loadIngests);
onMounted(loadIngests);

function changePage(delta: number) {
  page.value = Math.max(0, page.value + delta);
}
</script>
<template>
  <section>
    <h1 class="mb-4">Transfer ingests</h1>
    <BTable
      :items="ingests"
      :fields="columns"
      :busy="loading"
      head-variant="light"
      hover
      responsive
      show-empty
      empty-text="No ingests found.">
      <template #table-busy>
        <td :colspan="columns.length" class="text-body-secondary">Loading ingests…</td>
      </template>
      <template #cell(ingest_id)="{ item }">
        <code class="ingest-id">{{ item.ingest_id ?? '—' }}</code>
      </template>
      <template #cell(status)="{ item }">
        <span class="badge" :class="statusOf(item).variant">{{ statusOf(item).text }}</span>
      </template>
      <template #cell(checkpoint_bytes)="{ value }">
        {{ formatBytes(value as number | null | undefined) }}
      </template>
      <template #cell(checkpoint_rows)="{ value }">
        {{ formatNumber(value as number | null | undefined, 0) }}
      </template>
      <template #cell(azure_link)="{ item }">
        <a
          v-if="item.azure_link"
          :href="item.azure_link"
          target="_blank"
          rel="noopener noreferrer"
          class="azure-link"
          >Result file</a
        >
        <span v-else class="text-body-secondary">—</span>
      </template>
    </BTable>
    <div class="d-flex">
      <nav class="d-flex align-items-center gap-3" aria-label="Ingest pages">
        <BButton
          size="sm"
          variant="outline-secondary"
          title="Previous page"
          :disabled="page === 0 || loading"
          @click="changePage(-1)">
          <BIconChevronLeft />
          Previous
        </BButton>
        <span class="small text-body-secondary">
          {{ page * pageLimit + (ingests.length ? 1 : 0) }}–{{
            page * pageLimit + ingests.length
          }}
          · page {{ page + 1 }}
        </span>
        <BButton
          size="sm"
          variant="outline-secondary"
          title="Next page"
          :disabled="ingests.length === 0 || loading"
          @click="changePage(1)">
          Next
          <BIconChevronRight />
        </BButton>
      </nav>
      <div class="d-flex flex-wrap align-items-end gap-2 ms-auto">
        <BFormCheckbox v-model="onlyOpen" class="mb-2">Open ingests only</BFormCheckbox>
        <div>
          <label class="form-label small text-body-secondary mb-1" for="ingest-page-limit">
            Per page
          </label>
          <BFormSelect
            id="ingest-page-limit"
            v-model="pageLimit"
            :options="PAGE_SIZES"
            size="sm"
            class="w-auto" />
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.btn svg {
  vertical-align: -0.125em;
}

.ingest-id {
  /* Ingest ids are long uuids; let them break so the table stays readable. */
  word-break: break-all;
}

.azure-link {
  /* The links carry an auth key and are far too long to show in full. */
  display: inline-block;
  max-width: 12rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: bottom;
}
</style>
