<script setup lang="ts">
import {
  type AccessTokenPublic,
  Tokens,
  type UserTokenPermission,
  type Wrapper,
} from '@/plugins/api/spec';
import { computed, onMounted, ref } from 'vue';
import { toastReject } from '@/plugins/api';
import TokenInfo from '@/components/TokenInfo.vue';
import { useSessionStore } from '@/stores/session.ts';

const tokens = ref<AccessTokenPublic[]>([]);
const tokenPermissions = ref<UserTokenPermission[]>([]);
const session = useSessionStore();

onMounted(() => {
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
});
const permissionLookup = computed(() =>
  Object.fromEntries(tokenPermissions.value.map((permission) => [permission.token_id, permission])),
);
function removeToken(deleted: AccessTokenPublic) {
  tokens.value = tokens.value.filter((token) => token.token_id !== deleted.token_id);
}
const tokensByWrapper = computed(() => {
  const groups = new Map<Wrapper, AccessTokenPublic[]>();
  tokens.value.forEach((token) => {
    const group = groups.get(token.wrapper);
    if (group) group.push(token);
    else groups.set(token.wrapper, [token]);
  });
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([wrapper, group]) => ({
      wrapper,
      tokens: group.sort((a, b) => {
        // Tokens without a comment sort after those that have one.
        if (!a.comment) return b.comment ? 1 : 0;
        if (!b.comment) return -1;
        return a.comment.localeCompare(b.comment);
      }),
    }));
});
</script>
<template>
  <section>
    <h1>Tokens</h1>
    <p class="text-body-secondary">TODO: manage API tokens.</p>
    <section v-for="group in tokensByWrapper" :key="group.wrapper" class="mb-4">
      <h6 class="text-body-secondary text-uppercase small mb-2">{{ group.wrapper }}</h6>
      <TokenInfo
        v-for="token in group.tokens"
        :key="token.token_id"
        :token="token"
        :permissions="permissionLookup"
        :current-user="session.user"
        class="mb-2"
        @deleted="removeToken" />
    </section>
  </section>
</template>
