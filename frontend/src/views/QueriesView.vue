<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { toastReject } from '@/plugins/api';
import { Queries, type Query } from '@/plugins/api/spec';
import { formatCreatedAt } from '@/utils';

/** Longer queries are shortened to this many characters in the table. */
const PREVIEW_LENGTH = 10;

const router = useRouter();
const queries = ref<Query[]>([]);
const loading = ref(true);
/** Full text of the query shown in the modal; `null` while the modal is closed. */
const fullQuery = ref<string | null>(null);
const modal = ref<HTMLElement | null>(null);

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

// Move focus into the dialog when it opens, so Escape and the tab order stay with it.
watch(fullQuery, async (query) => {
  if (query === null) return;
  await nextTick();
  modal.value?.focus();
});

/** The table only shows the start of long queries; the modal shows all of them. */
function preview(query: string): string {
  return query.length > PREVIEW_LENGTH ? `${query.slice(0, PREVIEW_LENGTH)}...` : query;
}

function isTruncated(query: string): boolean {
  return query.length > PREVIEW_LENGTH;
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
    <div class="table-responsive">
      <table class="table table-hover align-middle">
        <thead class="table-light">
          <tr>
            <th scope="col">ID</th>
            <th scope="col">Query</th>
            <th scope="col">Wrapper</th>
            <th scope="col">Created At</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="query in queries"
            :key="query.query_id ?? query.query"
            :class="{ 'is-link': query.query_id != null }"
            @click="openTokens(query)">
            <td>
              <span class="fw-bold">{{ query.query_id }}</span>
            </td>
            <td>
              {{ preview(query.query) }}
              <span
                v-if="isTruncated(query.query)"
                class="show-full small text-muted ms-1"
                role="button"
                tabindex="0"
                @click.stop="fullQuery = query.query"
                @keyup.enter.stop="fullQuery = query.query">
                [show full]
              </span>
            </td>
            <td>
              <code>{{ query.wrapper }}</code>
            </td>
            <td class="text-muted small">{{ formatCreatedAt(query.created_at) }}</td>
          </tr>
          <tr v-if="loading">
            <td colspan="4" class="text-body-secondary">Loading queries…</td>
          </tr>
          <tr v-else-if="!queries.length">
            <td colspan="4" class="text-body-secondary">No queries yet.</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Full query modal -->
    <div
      v-if="fullQuery !== null"
      ref="modal"
      class="query-modal modal fade show d-block"
      tabindex="-1"
      role="dialog"
      aria-modal="true"
      aria-labelledby="full-query-title"
      @click.self="fullQuery = null"
      @keydown.esc="fullQuery = null">
      <div class="modal-dialog modal-lg modal-dialog-centered">
        <div class="modal-content">
          <div class="modal-header">
            <h5 id="full-query-title" class="modal-title">Full Query</h5>
            <button type="button" class="btn-close" aria-label="Close" @click="fullQuery = null" />
          </div>
          <div class="modal-body">
            <pre class="full-query bg-light p-3 border rounded">{{ fullQuery }}</pre>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary" @click="fullQuery = null">Close</button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* Rows lead to another view, so they invite a click. */
tbody tr.is-link {
  cursor: pointer;
}

.show-full {
  cursor: pointer;
}

/* Bootstrap paints its backdrop with JavaScript, which this app does not load,
   so the dialog shades the page behind it itself. */
.query-modal {
  background: rgba(0, 0, 0, 0.5);
}

.full-query {
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
