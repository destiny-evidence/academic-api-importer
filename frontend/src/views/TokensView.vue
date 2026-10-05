<script setup lang="ts">
import {
  type AccessToken,
  type AccessTokenPublic,
  Tokens,
  type UserTokenPermission,
  Wrapper,
} from '@/plugins/api/spec';
import { computed, onMounted, ref } from 'vue';
import { formatNumber, formatTimeStamp } from '@/utils';
import { updateTokenStatus } from '@/plugins/api/helpers.ts';
import { toastReject, toastSuccess, toastSuccessWith } from '@/plugins/api';
import TwoStageButton from '@/components/TwoStageButton.vue';
import ModalDialog from '@/components/ModalDialog.vue';
import { BButton, BFormCheckbox, BFormInput, BFormSelect } from 'bootstrap-vue-next/components';
import { BIconInfoCircle, BIconInfoCircleFill, BIconPlus } from 'bootstrap-icons-vue';
import { useSessionStore } from '@/stores/store-session';

type Status = {
  capacity?: number;
  remaining?: number;
  remainingPercent?: number;
  status?: string;
  capacityReset?: string;
};
type TokenInfo = {
  token: AccessTokenPublic;
  permission?: UserTokenPermission | null;
  status?: Status | null;
  isEditable: boolean;
};

const tokens = ref<AccessTokenPublic[]>([]);
const tokenPermissions = ref<UserTokenPermission[]>([]);
const session = useSessionStore();

function reload() {
  Tokens.listTokensTokensListGet()
    .then((response) => {
      tokens.value = response.data;
    })
    .catch(toastReject);
  Tokens.listPermissionsTokensPermissionGet()
    .then((response) => {
      tokenPermissions.value = response.data;
    })
    .catch(toastReject);
}

onMounted(reload);
const permissionLookup = computed(() =>
  Object.fromEntries(tokenPermissions.value.map((permission) => [permission.token_id, permission])),
);

function removeToken(deleted: AccessTokenPublic) {
  tokens.value = tokens.value.filter((token) => token.token_id !== deleted.token_id);
}

const tokensByWrapper = computed(() => {
  const groups = new Map<Wrapper, TokenInfo[]>();
  tokens.value.forEach((token) => {
    const group = groups.get(token.wrapper);
    const info: TokenInfo = {
      token,
      status: tokenStatus(token),
      isEditable: hasEditPermission(token),
    };
    if (group) group.push(info);
    else groups.set(token.wrapper, [info]);
  });
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([wrapper, group]) => ({
      wrapper,
      tokens: group.sort((a, b) => {
        // Tokens without a comment sort after those that have one.
        if (!a.token.comment) return b.token.comment ? 1 : 0;
        if (!b.token.comment) return -1;
        return a.token.comment.localeCompare(b.token.comment);
      }),
    }));
});

/** Token IDs whose raw API feedback is expanded below their row. */
const showMetaTokens = ref<Record<string, boolean>>({});

function hasEditPermission(token: AccessTokenPublic): boolean {
  const currentUser = session.user;
  const permissions = permissionLookup.value;
  return Boolean(
    currentUser?.is_active &&
    (currentUser?.permission_admin ||
      (token.token_id &&
        permissions[token.token_id] &&
        permissions[token.token_id].user_id == currentUser?.user_id)),
  );
}

function tokenStatus(token: AccessTokenPublic): Status {
  if (token.wrapper === 'scopus' && token.last_feedback) {
    return {
      capacity: token.last_feedback.scopus_requests_limit as number | undefined,
      remaining: token.last_feedback.scopus_requests_remaining as number | undefined,
      remainingPercent:
        token.last_feedback.scopus_requests_remaining && token.last_feedback.scopus_requests_limit
          ? ((token.last_feedback.scopus_requests_remaining as number) /
              (token.last_feedback.scopus_requests_limit as number)) *
            100
          : undefined,
      status: token.last_feedback.status as string | undefined,
      capacityReset: token.last_feedback.scopus_requests_reset
        ? formatTimeStamp((token.last_feedback.scopus_requests_reset as number) * 1000)
        : undefined,
    };
  }
  return {};
}

/** The lock is over once `locked_until` lies in the past; while it is in the
 *  future the token cannot be used, so highlight it. */
function lockedUntilClass(token: AccessTokenPublic) {
  if (!token.locked_until) return 'text-body-secondary';
  const lockedUntil = Date.parse(token.locked_until);
  if (Number.isNaN(lockedUntil)) return '';
  return lockedUntil <= Date.now() ? 'text-success' : 'text-danger';
}

function updateStatus(token: AccessTokenPublic, active: boolean) {
  if (!token.token_id) return;
  updateTokenStatus(token.token_id, active)
    .then(() => {
      // Keep the listing in step with the API so the switch shows the
      // stored state.
      token.is_active = active;
    })
    .catch(toastReject);
}

function deleteToken(token: AccessTokenPublic) {
  if (!token.token_id) return;
  Tokens.deleteTokenTokensDeleteDelete({ token_id: token.token_id })
    .then((result) => {
      removeToken(token);
      return result;
    })
    .then(toastSuccess)
    .catch(toastReject);
}

/** Draft for the "Add API token" dialog; `wrapper` stays empty until chosen. */
type NewToken = { wrapper: Wrapper | ''; token: string; comment: string; proxy: string };
const newToken = ref<NewToken>({ wrapper: '', token: '', comment: '', proxy: '' });
const addTokenOpen = ref(false);
const savingToken = ref(false);

const wrappers = Object.values(Wrapper);

/* The endpoint rejects empty `token` and `comment`, so do not offer saving. */
const canSaveToken = computed(
  () =>
    Boolean(newToken.value.wrapper) &&
    Boolean(newToken.value.token.trim()) &&
    Boolean(newToken.value.comment.trim()),
);

function openAddToken() {
  newToken.value = { wrapper: '', token: '', comment: '', proxy: '' };
  addTokenOpen.value = true;
}

/**
 * Sends the draft to `/tokens/submit`, which stores the token and also grants
 * the current user ownership (`UserTokenPermission.is_owner`); reloading both
 * listings therefore makes the new token show up right away, already editable.
 */
function addToken() {
  if (!canSaveToken.value || savingToken.value) return;
  savingToken.value = true;
  const { wrapper, token, comment, proxy } = newToken.value;
  Tokens.addTokenTokensSubmitPost({
    accessToken: {
      wrapper: wrapper as Wrapper,
      token: token.trim(),
      comment: comment.trim(),
      proxy: proxy.trim() || null,
    } as AccessToken,
  })
    .then((result) => {
      addTokenOpen.value = false;
      reload();
      return result;
    })
    .then(toastSuccessWith('Token added'))
    .catch(toastReject)
    .finally(() => {
      savingToken.value = false;
    });
}
</script>
<template>
  <section>
    <div class="d-flex align-items-center justify-content-between mb-3">
      <h1 class="mb-0">Tokens</h1>
      <BButton variant="primary" title="Add a new API token" @click="openAddToken">
        <BIconPlus /> Add token
      </BButton>
    </div>
    <p class="text-body-secondary">TODO: manage API tokens.</p>
    <section v-for="group in tokensByWrapper" :key="group.wrapper" class="mb-4">
      <h6 class="text-body-secondary text-uppercase small mb-2">{{ group.wrapper }}</h6>
      <div class="table-responsive">
        <table class="table table-sm align-middle">
          <thead>
            <tr>
              <th scope="col">Token</th>
              <th scope="col">Description</th>
              <th scope="col">Last used</th>
              <th scope="col">Locked until</th>
              <th scope="col">Active</th>
              <th scope="col">Capacity</th>
              <th scope="col" class="text-end">Actions</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="token in group.tokens" :key="token.token.token_id">
              <tr>
                <td>
                  <code>{{ token.token.token_id }}</code>
                </td>
                <td>{{ token.token.comment }}</td>
                <td>{{ formatTimeStamp(token.token.last_used) }}</td>
                <td :class="lockedUntilClass(token.token)" title="Locked until">
                  {{ formatTimeStamp(token.token.locked_until) }}
                </td>
                <td>
                  <BFormCheckbox
                    v-if="token.isEditable"
                    :model-value="Boolean(token.token.is_active)"
                    switch
                    title="Toggle the token's active state"
                    @update:model-value="(active) => updateStatus(token.token, Boolean(active))">
                    Active
                  </BFormCheckbox>
                  <template v-else>
                    {{ token.token.is_active }}
                  </template>
                </td>
                <td class="text-nowrap">
                  <template v-if="token.status">
                    <div>
                      {{ formatNumber(token.status.remaining) }} /
                      {{ formatNumber(token.status.capacity) }}
                    </div>
                    <div class="d-flex flex-row align-items-center">
                      <div
                        v-if="token.token.last_feedback"
                        class="progress w-100"
                        role="progressbar"
                        aria-label="Access token capacity"
                        :aria-valuenow="token.status.remainingPercent"
                        aria-valuemin="0"
                        aria-valuemax="100">
                        <div
                          class="progress-bar text-bg-secondary"
                          :style="{ width: token.status.remainingPercent + '%' }">
                          {{ formatNumber(token.status.remainingPercent) }}%
                        </div>
                      </div>
                      <BButton
                        v-if="token.token.last_feedback"
                        size="sm"
                        variant="link"
                        class="p-0 ms-2 text-body-secondary"
                        :title="
                          showMetaTokens[token.token.token_id!]
                            ? 'Hide raw API feedback'
                            : 'Show raw API feedback'
                        "
                        @click="
                          showMetaTokens[token.token.token_id!] =
                            !showMetaTokens[token.token.token_id!]
                        ">
                        <BIconInfoCircleFill v-if="showMetaTokens[token.token.token_id!]" />
                        <BIconInfoCircle v-else />
                      </BButton>
                    </div>
                  </template>
                </td>
                <td class="text-end text-nowrap">
                  <TwoStageButton
                    v-if="token.isEditable"
                    btn-style="btn-outline-danger"
                    @accepted="deleteToken(token.token)">
                    Delete
                  </TwoStageButton>
                </td>
              </tr>
              <tr v-if="token.token.token_id && showMetaTokens[token.token.token_id]">
                <td colspan="7">
                  <pre class="mb-0">{{ token.token.last_feedback }}</pre>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </section>

    <ModalDialog v-model="addTokenOpen" title="Add API token">
      <form class="row g-3" @submit.prevent="addToken">
        <div class="col-12">
          <label class="form-label small text-body-secondary mb-1" for="new-token-wrapper">
            Wrapper
          </label>
          <BFormSelect id="new-token-wrapper" v-model="newToken.wrapper" required>
            <option value="" disabled>Select a wrapper…</option>
            <option v-for="wrapper in wrappers" :key="wrapper" :value="wrapper">
              {{ wrapper }}
            </option>
          </BFormSelect>
        </div>
        <div class="col-12">
          <label class="form-label small text-body-secondary mb-1" for="new-token-secret">
            Token
          </label>
          <BFormInput
            id="new-token-secret"
            v-model="newToken.token"
            type="text"
            autocomplete="off"
            spellcheck="false"
            placeholder="The secret API token"
            required />
        </div>
        <div class="col-12">
          <label class="form-label small text-body-secondary mb-1" for="new-token-comment">
            Description
          </label>
          <BFormInput id="new-token-comment" v-model="newToken.comment" required />
        </div>
        <div class="col-12">
          <label class="form-label small text-body-secondary mb-1" for="new-token-proxy">
            Proxy
          </label>
          <BFormInput
            id="new-token-proxy"
            v-model="newToken.proxy"
            autocomplete="off"
            placeholder="http://proxy.example.org:3128 (optional)" />
        </div>
      </form>
      <template #footer>
        <BButton variant="secondary" @click="addTokenOpen = false">Cancel</BButton>
        <BButton
          variant="success"
          :disabled="!canSaveToken || savingToken"
          title="Store the token and grant yourself ownership"
          @click="addToken">
          Save
        </BButton>
      </template>
    </ModalDialog>
  </section>
</template>

<style scoped>
.btn svg {
  vertical-align: -0.125em;
}
</style>
