<script setup lang="ts">
import {
  type AccessTokenPublic,
  Tokens,
  type UserPublic,
  type UserTokenPermission,
} from '@/plugins/api/spec';
import { computed, ref } from 'vue';
import { formatNumber, formatTimeStamp } from '@/utils';
import { updateTokenStatus } from '@/plugins/api/helpers.ts';
import TwoStageButton from '@/components/TwoStageButton.vue';
import { toastReject, toastSuccess } from '@/plugins/api';
import { BButton, BFormCheckbox } from 'bootstrap-vue-next/components';
import { BIconInfoCircle, BIconInfoCircleFill } from 'bootstrap-icons-vue';

type Status = {
  capacity?: number;
  remaining?: number;
  remainingPercent?: number;
  status?: string;
  capacityReset?: string;
};

const { token, permissions, currentUser } = defineProps<{
  token: AccessTokenPublic;
  permissions: Record<string, UserTokenPermission>;
  currentUser?: UserPublic | null;
}>();

/** Emitted after the API removed the token, so the parent can drop it. */
const emit = defineEmits<{ deleted: [deletedToken: AccessTokenPublic] }>();

const showMeta = ref(false);
const hasEditPermission = computed(() => {
  return (
    currentUser?.is_active &&
    (currentUser?.permission_admin ||
      (permissions &&
        token.token_id &&
        permissions[token.token_id] &&
        permissions[token.token_id].user_id == currentUser?.user_id))
  );
});

const info = computed<Status | null>(() => {
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
});

/** The lock is over once `locked_until` lies in the past; while it is in the
 *  future the token cannot be used, so highlight it. */
const lockedUntilClass = computed(() => {
  if (!token.locked_until) return 'text-body-secondary';
  const lockedUntil = Date.parse(token.locked_until);
  if (Number.isNaN(lockedUntil)) return '';
  return lockedUntil <= Date.now() ? 'text-success' : 'text-danger';
});

function updateStatus(active: boolean) {
  if (!token.token_id) return;
  updateTokenStatus(token.token_id, active)
    .then(() => {
      // The parent owns the listing this token object came from; keep it in
      // step with the API so the switch shows the stored state.
      token.is_active = active;
    })
    .catch(toastReject);
}

function deleteToken() {
  if (!token.token_id) return;
  Tokens.deleteTokenTokensDeleteDelete({ token_id: token.token_id })
    .then((result) => {
      emit('deleted', token);
      return result;
    })
    .then(toastSuccess)
    .catch(toastReject);
}
</script>

<template>
  <div>
    <div class="row">
      <div class="col-8 d-flex flex-wrap align-items-center gap-2">
        <code>{{ token.token_id }}</code>
        {{ token.comment }}
        {{ formatTimeStamp(token.last_used) }}

        <span :class="lockedUntilClass" title="Locked until">{{
          formatTimeStamp(token.locked_until)
        }}</span>
        <!-- {{token.proxy}} -->

        <BFormCheckbox
          v-if="hasEditPermission"
          :model-value="Boolean(token.is_active)"
          switch
          title="Toggle the token's active state"
          @update:model-value="(active) => updateStatus(Boolean(active))">
          Active
        </BFormCheckbox>
        <template v-else>
          {{ token.is_active }}
        </template>

        <TwoStageButton
          v-if="hasEditPermission"
          btn-style="btn-outline-danger"
          @accepted="deleteToken()"
          >Delete
        </TwoStageButton>
      </div>
      <div v-if="info" class="col-4">
        <div class="d-flex flex-column align-items-end text-end">
          <BButton
            v-if="token.last_feedback"
            size="sm"
            variant="link"
            class="p-0 mb-1 text-body-secondary"
            :title="showMeta ? 'Hide raw API feedback' : 'Show raw API feedback'"
            @click="showMeta = !showMeta">
            <BIconInfoCircleFill v-if="showMeta" />
            <BIconInfoCircle v-else />
          </BButton>
          <div>{{ formatNumber(info.remaining) }} / {{ formatNumber(info.capacity) }}</div>
          <div
            class="progress w-100"
            role="progressbar"
            aria-label="Access token capacity"
            :aria-valuenow="info.remainingPercent"
            aria-valuemin="0"
            aria-valuemax="100">
            <div
              class="progress-bar text-bg-secondary"
              :style="{ width: info.remainingPercent + '%' }">
              {{ formatNumber(info.remainingPercent) }}%
            </div>
          </div>
        </div>
      </div>
    </div>
    <div v-if="showMeta">
      <pre>
         {{ token.last_feedback }}
      </pre>
    </div>
  </div>
</template>

<style scoped>
.btn svg {
  vertical-align: -0.125em;
}
</style>
