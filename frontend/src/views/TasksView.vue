<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { BButton, BFormSelect } from 'bootstrap-vue-next/components';
import {
  BIconChevronLeft,
  BIconChevronRight,
  BIconInfoCircle,
  BIconInfoCircleFill,
} from 'bootstrap-icons-vue';

import { toastReject } from '@/plugins/api';
import { type QueryProgress, Tasks, type TaskPublic, TaskState } from '@/plugins/api/spec';
import { formatNumber, formatTimeStamp } from '@/utils';

/** Page sizes offered by the page size selector. */
const PAGE_SIZES = [10, 20, 50, 100];

/** Badge colour for each task state; tasks without one stay grey. */
const STATUS_VARIANTS: Record<TaskState, string> = {
  [TaskState.STARTED]: 'text-bg-primary',
  [TaskState.RETRY]: 'text-bg-warning',
  [TaskState.SUCCESS]: 'text-bg-success',
  [TaskState.FAILURE]: 'text-bg-danger',
};

const route = useRoute();

const selectedQueryId = ref<number | null>(null);

/** '' keeps "all statuses" selectable in the filter dropdown. */
const statusFilter = ref<TaskState | ''>('');
const progress = ref<QueryProgress[]>([]);
const tasks = ref<TaskPublic[]>([]);
const loading = ref(true);
const page = ref<number>(0);
const pageLimit = ref<number>(20);

/** Task IDs whose raw progress payload is expanded below their row. */
const showTaskProgressField = ref<Record<string, boolean>>({});

const statusOptions = [
  { value: '' as const, text: 'All statuses' },
  ...Object.values(TaskState).map((status) => ({ value: status, text: status })),
];

/** Summed over the per-status summary rows, for the table's footer. */
const progressTotals = computed(() =>
  progress.value.reduce(
    (totals, entry) => ({
      n_tasks: totals.n_tasks + entry.n_tasks,
      n_retrieved: totals.n_retrieved + entry.n_retrieved,
      n_results: totals.n_results + entry.n_results,
    }),
    { n_tasks: 0, n_retrieved: 0, n_results: 0 },
  ),
);

function statusVariant(status?: TaskState | null): string {
  return status ? STATUS_VARIANTS[status] : 'text-bg-secondary';
}

/** Only tasks that reported something are worth expanding. */
function hasProgress(task: TaskPublic): boolean {
  return Object.keys(task.progress ?? {}).length > 0;
}

function loadProgress() {
  if (!selectedQueryId.value) {
    progress.value = [];
    return;
  }
  Tasks.taskSummaryTasksSummaryGet({ query_id: selectedQueryId.value })
    .then(({ data }) => {
      progress.value = data ?? [];
    })
    .catch(toastReject);
}

function loadTasks() {
  loading.value = true;
  Tasks.listTasksTasksGet({
    query_id: selectedQueryId.value || null,
    offset: pageLimit.value * page.value,
    limit: pageLimit.value,
    status: statusFilter.value || null,
  })
    .then(({ data }) => {
      showTaskProgressField.value = {};
      tasks.value = data ?? [];
    })
    .catch(toastReject)
    .finally(() => {
      loading.value = false;
    });
}

// Changing what is listed starts over on the first page. This watcher runs
// before the one below, so the reset already happened when that one loads.
watch([selectedQueryId, statusFilter], () => (page.value = 0));
watch([selectedQueryId, statusFilter, page, pageLimit], loadTasks);
watch(selectedQueryId, loadProgress);

onMounted(() => {
  try {
    const raw = Array.isArray(route.params.query_id)
      ? route.params.query_id[0]
      : route.params.query_id;
    const queryId = Number(raw);
    selectedQueryId.value = Number.isInteger(queryId) && queryId > 0 ? queryId : null;
  } catch {
    selectedQueryId.value = null;
  }

  loadTasks();
  loadProgress();
});

function changePage(delta: number) {
  page.value = Math.max(0, page.value + delta);
}
</script>
<template>
  <section>
    <div class="d-flex align-items-center justify-content-between mb-3">
      <h1 class="mb-0">
        Tasks for query #<input
          v-model="selectedQueryId"
          style="width: 4em; font-size: 1em"
          type="text"
          class="form-control d-inline" />
      </h1>
    </div>

    <section v-if="selectedQueryId" class="mb-4">
      <h6 class="text-body-secondary text-uppercase small mb-2">Progress</h6>
      <p v-if="!progress.length" class="text-body-secondary">No tasks for this query yet.</p>
      <div v-else class="table-responsive w-auto">
        <table class="table table-sm align-middle">
          <thead>
            <tr>
              <th scope="col">Status</th>
              <th scope="col" class="text-end">Tasks</th>
              <th scope="col" class="text-end">Retrieved</th>
              <th scope="col" class="text-end">Results</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in progress" :key="entry.status ?? 'none'">
              <td>
                <span class="badge" :class="statusVariant(entry.status)">{{
                  entry.status ?? '—'
                }}</span>
              </td>
              <td class="text-end">{{ formatNumber(entry.n_tasks, 0) }}</td>
              <td class="text-end">{{ formatNumber(entry.n_retrieved, 0) }}</td>
              <td class="text-end">{{ formatNumber(entry.n_results, 0) }}</td>
            </tr>
          </tbody>
          <tfoot class="fw-bold">
            <tr>
              <td>All</td>
              <td class="text-end">{{ formatNumber(progressTotals.n_tasks, 0) }}</td>
              <td class="text-end">{{ formatNumber(progressTotals.n_retrieved, 0) }}</td>
              <td class="text-end">{{ formatNumber(progressTotals.n_results, 0) }}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>

    <div class="table-responsive">
      <table class="table table-sm align-middle">
        <thead>
          <tr>
            <th scope="col">Task</th>
            <th scope="col">Query</th>
            <th scope="col">Subquery</th>
            <th scope="col" class="text-end">Retries</th>
            <th scope="col">Status</th>
            <th scope="col" class="text-end">Execution time</th>
            <th scope="col">Dates</th>
            <th scope="col" class="text-end">Progress</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="8" class="text-body-secondary">Loading tasks…</td>
          </tr>
          <tr v-else-if="!tasks.length">
            <td colspan="8" class="text-body-secondary">No tasks found.</td>
          </tr>
          <template v-else>
            <template v-for="task in tasks" :key="task.task_id">
              <tr>
                <td>
                  <code class="task-id">{{ task.task_id }}</code>
                </td>
                <td>{{ task.query_id }}</td>
                <td class="subquery">{{ task.subquery }}</td>
                <td class="text-end">{{ task.num_retries ?? 0 }}</td>
                <td>
                  <span class="badge" :class="statusVariant(task.status)">
                    {{ task.status ?? '—' }}
                  </span>
                </td>
                <td class="text-end text-nowrap">
                  {{ formatNumber(task.execution_time, 2) }}&thinsp;s
                </td>
                <td class="date-stack text-nowrap">
                  <div>
                    <span class="date-label">created</span>{{ formatTimeStamp(task.created_at) }}
                  </div>
                  <div>
                    <span class="date-label">last try</span>{{ formatTimeStamp(task.latest_try) }}
                  </div>
                  <div>
                    <span class="date-label">finished</span>{{ formatTimeStamp(task.finished_at) }}
                  </div>
                </td>
                <td class="text-end text-nowrap">
                  <BButton
                    v-if="hasProgress(task)"
                    size="sm"
                    variant="link"
                    class="text-body-secondary"
                    :title="
                      showTaskProgressField[task.task_id!]
                        ? 'Hide task progress'
                        : 'Show task progress'
                    "
                    @click="
                      showTaskProgressField[task.task_id!] = !showTaskProgressField[task.task_id!]
                    ">
                    <BIconInfoCircleFill v-if="showTaskProgressField[task.task_id!]" />
                    <BIconInfoCircle v-else />
                  </BButton>
                </td>
              </tr>
              <tr v-if="hasProgress(task) && showTaskProgressField[task.task_id!]">
                <td colspan="8">
                  <pre class="task-progress bg-light p-3 border rounded mb-0">{{
                    task.progress
                  }}</pre>
                </td>
              </tr>
            </template>
          </template>
        </tbody>
      </table>
    </div>
    <div class="d-flex">
      <nav class="d-flex align-items-center gap-3" aria-label="Task pages">
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
          {{ page * pageLimit + (tasks.length ? 1 : 0) }}–{{ page * pageLimit + tasks.length }} ·
          page {{ page + 1 }}
        </span>
        <BButton
          size="sm"
          variant="outline-secondary"
          title="Next page"
          :disabled="tasks.length === 0 || loading"
          @click="changePage(1)">
          Next
          <BIconChevronRight />
        </BButton>
      </nav>
      <div class="d-flex flex-wrap align-items-end gap-2 ms-auto">
        <div>
          <label class="form-label small text-body-secondary mb-1" for="task-status-filter">
            Status
          </label>
          <BFormSelect
            id="task-status-filter"
            v-model="statusFilter"
            :options="statusOptions"
            size="sm"
            class="w-auto" />
        </div>
        <div>
          <label class="form-label small text-body-secondary mb-1" for="task-page-limit">
            Per page
          </label>
          <BFormSelect
            id="task-page-limit"
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

.task-id {
  /* Task ids are long uuids; let them break so the table stays readable. */
  word-break: break-all;
}

.subquery {
  /* Longest subqueries are kept at 50 characters by the API, still wide enough
     to be worth wrapping instead of pushing the other columns apart. */
  max-width: 24rem;
  word-break: break-word;
}

.date-stack {
  font-size: 0.8rem;
  line-height: 1.2;
  color: var(--bs-secondary-color);
}

.date-label {
  display: inline-block;
  width: 4.5rem;
  text-transform: uppercase;
  font-size: 0.65rem;
  opacity: 0.75;
}

.task-progress {
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
