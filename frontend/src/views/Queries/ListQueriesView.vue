<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { BButton, BFormCheckbox, BTable } from 'bootstrap-vue-next/components';
import {
  BIconArrowClockwise,
  BIconCloudArrowUp,
  BIconCloudArrowUpFill,
  BIconInboxFill,
  BIconLink,
  BIconPersonFill,
  BIconStars,
  BIconTrash,
  BIconDatabaseAdd,
} from 'bootstrap-icons-vue';
import type { TableField } from 'bootstrap-vue-next/types';

import ModalDialog from '@/components/ModalDialog.vue';
import { toastReject, toastSuccessWith } from '@/plugins/api';
import {
  Queries,
  type Query, Transfer,
  type UserPublicBase,
  type UserQueryPermission,
  Users,
} from '@/plugins/api/spec';
import { formatNumber, formatTimeStamp } from '@/utils';
import TwoStageButton from '@/components/TwoStageButton.vue';
import { useSessionStore } from '@/stores/store-session.ts';
import RefreshIcon from '@/components/RefreshIcon.vue';

const session = useSessionStore();
// Longer queries are shortened to this many characters in the table.
const PREVIEW_LENGTH = 50;
type TableQuery = Query & { showLinks?: boolean };
const router = useRouter();
const queries = ref<TableQuery[]>([]);
const loadingQueries = ref(true);
const loadingUsers = ref(false);
// Full text of the query shown in the modal; `null` while the modal is closed.
const fullQuery = ref<string | null>(null);

// The modal tracks its open state through `v-model`; closing it clears the text
// so `fullQuery === null` remains the one truth about whether it is closed.
const modalQueryOpen = computed({
  get: () => fullQuery.value !== null,
  set: (isOpen) => {
    if (!isOpen) fullQuery.value = null;
  },
});

const users = ref<UserPublicBase[]>([]);
/** Query whose permissions dialog is open; `null` while the modal is closed. */
const permissionQuery = ref<TableQuery | null>(null);
/**
 * Known permissions for `permissionQuery`. No endpoint asks for them, so the
 * dialog starts out blank; both mutation endpoints answer with the query's
 * full permission list, so every edit rebases the switches on stored state.
 */
const queryPermissions = ref<UserQueryPermission[]>([]);

const permissionModalOpen = computed({
  get: () => permissionQuery.value !== null,
  set: (isOpen) => {
    if (!isOpen) {
      permissionQuery.value = null;
      queryPermissions.value = [];
    }
  },
});

function openPermissions(query: Query) {
  if (query.query_id == null) return;
  if (users.value.length === 0) loadUsers();
  permissionQuery.value = query;
  Queries.getPermissionsQueriesPermissionGet({ query_id: query.query_id })
    .then(({ data }) => (queryPermissions.value = data))
    .catch(toastReject);
}

function loadUsers() {
  // Don't load again if we already have users loaded.
  if (users.value.length > 0) return;
  loadingUsers.value = true;
  Users.listUserBasesUsersBaseGet()
    .then(({ data }) => {
      users.value = data ?? [];
    })
    .catch(toastReject)
    .finally(() => {
      loadingUsers.value = false;
    });
}

function permissionFor(userId?: string): UserQueryPermission | undefined {
  return userId ? queryPermissions.value.find((p) => p.user_id === userId) : undefined;
}

/**
 * Sends a user's permission on the current query: upserting grants access or
 * changes ownership, dropping revokes access. Both responses carry the query's
 * permissions as now stored, so the switches resync after every edit.
 */
function updatePermission(user: UserPublicBase, access: boolean, owner = false) {
  const queryId = permissionQuery.value?.query_id;
  if (queryId == null || !user.user_id) return;
  const userQueryPermission: UserQueryPermission = {
    user_id: user.user_id,
    query_id: queryId,
    is_owner: owner,
  };
  const request = access
    ? Queries.upsertPermissionQueriesPermissionPut(
      { userQueryPermission },
      { credentials: 'include' },
    )
    : Queries.dropPermissionQueriesPermissionDelete(
      { userQueryPermission },
      { credentials: 'include' },
    );
  request
    .then((result) => {
      queryPermissions.value = result.data ?? [];
      return result;
    })
    .then(toastSuccessWith('Permissions updated'))
    .catch(toastReject);
}

// Keys double as `Query` property names, so the table sorts the raw values
// even where a slot or formatter changes how a cell is displayed. The one
// exception is `permissions`, a column without item data that only its slot
// fills.
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
  { key: 'actions', label: 'Actions' },
];

function loadQueries() {
  loadingQueries.value = true;
  Queries.listQueriesQueriesGet({}, { credentials: 'include' })
    .then(({ data }) => (queries.value = data ?? []))
    .catch(toastReject)
    .finally(() => {
      loadingQueries.value = false;
    });
}

onMounted(loadQueries);

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
function openTasks(query: Query) {
  if (query.query_id == null) return;
  router.push({ name: 'tasks', params: { query_id: query.query_id } });
}

function dropQuery(query: Query) {
  Queries.deleteQueryQueriesDelete({ query_id: query.query_id as number })
    .then(toastSuccessWith(`Marked query ${query.query_id} for deletion.`))
    .catch(toastReject);
  const index = queries.value.findIndex((q) => query.query_id == q.query_id);
  if (index >= 0) {
    queries.value.slice(index, 1);
  }
}

function syncQuery(query: Query, concatenated: boolean = false) {
  Queries.downloadQueryQueriesDownloadGet({ query_id: query.query_id!, concatenated })
    .then((response) => {
      query.azure_link = response.data;
      toastSuccessWith('Query results are now syncing with Azure, this might take a while.')(
        response,
      );
    })
    .catch(toastReject);
}

function transferQuery(query: Query) {
  Transfer.addIngestIngestTransferPut({ query_id: query.query_id! })
    .then(toastSuccessWith('Query marked for transfer worker.'))
    .catch(toastReject);
}

function clearLogs(query: Query) {
  Queries.deleteQueryLogsQueriesLogsDelete({ query_id: query.query_id! })
    .then(toastSuccessWith('Initiated deletion of query logs.'))
    .catch(toastReject);
}

function refreshAzureToken(query: Query) {
  Queries.refreshDownloadLinksQueriesDownloadRefreshGet({ query_id: query.query_id! })
    .then((response) => {
      query.azure_link = response.data;
      toastSuccessWith('Access links updated.')(response);
    })
    .catch(toastReject);
}
</script>

<template>
  <section>
    <h1 class="mb-4">Queries</h1>
    <BTable
      :items="queries"
      :fields="columns"
      :busy="loadingQueries"
      :tbody-tr-class="rowClass"
      head-variant="light"
      hover
      responsive
      show-empty
      sort-icon-left
      empty-text="No queries yet.">
      <template #table-busy>
        <td :colspan="columns.length" class="text-body-secondary">Loading queries…</td>
      </template>
      <template #cell(query)="{ item }">
        <div>
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
        </div>
        <div v-if="item.showLinks && item.azure_link">
          <BButton
            size="sm"
            class="me-2"
            variant="outline-secondary"
            title="Generate new access link (only use if current link stopped working)"
            @click.stop="refreshAzureToken(item)">
            <BIconArrowClockwise />
          </BButton>
          <ul v-if="item.azure_link">
            <li v-for="(url, key) in item.azure_link" :key="key">
              {{ key }}:
              <a :href="url as string" target="_blank"
              >{{ (url as string).substring(0, 100) }}...</a
              >
            </li>
          </ul>
        </div>
      </template>
      <template #cell(wrapper)="{ item }">
        <code>{{ item.wrapper }}</code>
      </template>
      <template #cell(actions)="{ item }">
        <BButton
          size="sm"
          class="me-2"
          variant="outline-secondary"
          title="Manage user access to this query"
          @click.stop="openPermissions(item)">
          <BIconPersonFill />
        </BButton>

        <BButton
          size="sm"
          class="me-2"
          variant="outline-secondary"
          title="View associated tasks"
          @click.stop="openTasks(item)">
          <BIconInboxFill />
        </BButton>

        <BButton
          v-if="item.azure_link"
          size="sm"
          class="me-2"
          variant="outline-secondary"
          title="View cloud storage links"
          @click.stop="item.showLinks = !item.showLinks">
          <BIconLink />
        </BButton>

        <TwoStageButton
          v-if="session.user?.permission_admin && (item.azure_progress ?? 1) < 0"
          class="d-inline me-2"
          btn-style="btn-sm btn-outline-secondary"
          title="Sync results with cloud"
          @accepted="syncQuery(item)">
          <BIconCloudArrowUp />
        </TwoStageButton>

        <TwoStageButton
          v-if="session.user?.permission_admin && (item.azure_progress ?? 1) < 0"
          class="d-inline me-2"
          btn-style="btn-sm btn-outline-secondary"
          title="Sync results with cloud (concatenated)"
          @accepted="syncQuery(item, true)">
          <BIconCloudArrowUpFill />
        </TwoStageButton>

        <span v-if="(item.azure_progress ?? -1) >= 0">
          Uploaded {{ formatNumber(item.azure_progress) }}
          <RefreshIcon :refresh="loadQueries" />
        </span>

        <TwoStageButton
          v-if="session.user?.permission_admin && (item.azure_progress ?? -1) >= 0"
          class="d-inline me-2"
          btn-style="btn-sm btn-outline-secondary"
          title="Transfer data from blob to ingest queue"
          @accepted="transferQuery(item)">
          <BIconDatabaseAdd />
        </TwoStageButton>

        <TwoStageButton
          class="d-inline me-2"
          btn-style="btn-sm btn-outline-secondary"
          title="Delete logs from downloading the query."
          @accepted="clearLogs(item)">
          <BIconStars />
        </TwoStageButton>

        <TwoStageButton
          class="d-inline"
          btn-style="btn-sm btn-outline-secondary"
          title="Delete query"
          @accepted="dropQuery(item)">
          <BIconTrash />
        </TwoStageButton>
      </template>
    </BTable>

    <!-- Full query modal -->
    <ModalDialog v-model="modalQueryOpen" title="Full Query" size="lg">
      <pre class="full-query bg-light p-3 border rounded">{{ fullQuery }}</pre>
    </ModalDialog>

    <!-- Query permissions modal -->
    <ModalDialog
      v-model="permissionModalOpen"
      :title="`User access to query #${permissionQuery?.query_id}`"
      size="lg">
      <p v-if="loadingUsers" class="text-body-secondary">Loading users…</p>
      <p v-else-if="!users.length" class="text-body-secondary">No users yet.</p>

      <div v-else class="table-responsive">
        <table class="table table-sm align-middle">
          <thead>
          <tr>
            <th scope="col">User</th>
            <th scope="col">Has access</th>
            <th scope="col">Is owner</th>
          </tr>
          </thead>
          <tbody>
          <tr v-for="user in users" :key="user.user_id">
            <td>
              {{ user.full_name }} (<code>{{ user.user_id }}</code
            >)
            </td>
            <td>
              <BFormCheckbox
                :model-value="Boolean(permissionFor(user.user_id))"
                switch
                title="Revoke access by switching this off"
                @update:model-value="(access) => updatePermission(user, Boolean(access))" />
            </td>
            <td>
              <BFormCheckbox
                :model-value="Boolean(permissionFor(user.user_id)?.is_owner)"
                :disabled="!permissionFor(user.user_id)"
                switch
                title="Ownership requires access"
                @update:model-value="(owner) => updatePermission(user, true, Boolean(owner))" />
            </td>
          </tr>
          </tbody>
        </table>
      </div>
    </ModalDialog>
  </section>
</template>

<style scoped>
.btn svg {
  vertical-align: -0.125em;
}

.show-full {
  cursor: pointer;
}

.full-query {
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
