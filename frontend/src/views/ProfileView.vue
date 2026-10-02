<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { BButton, BFormCheckbox, BFormInput } from 'bootstrap-vue-next/components';
import { BIconFloppy } from 'bootstrap-icons-vue';
import { type UserPublic, Users, type UserUpdate } from '@/plugins/api/spec';
import { postToast, toastReject, toastSuccess } from '@/plugins/api';

const user = ref<UserUpdate | null>(null);
const editPassword = ref(false);
function populateUpdatableUser(data: UserPublic) {
  user.value = {
    user_id: data.user_id as string,
    email: data.email,
    full_name: data.full_name,
    institution: data.institution,
    password_plain: null,
  };
}
onMounted(() => {
  Users.getMyInfoUsersMeGet()
    .then((result) => populateUpdatableUser(result.data))
    .catch(toastReject);
});

function save() {
  if (!user.value) {
    postToast('ERROR', 'No user data available to send.');
    return;
  }
  if (!editPassword.value) user.value.password_plain = null;
  Users.updateUserUsersPut({ userUpdate: user.value })
    .then((result) => {
      if (result.data) {
        populateUpdatableUser(result.data);
      }
      return result;
    })
    .then(toastSuccess)
    .catch(toastReject);
}
</script>

<template>
  <section>
    <h1>My account</h1>
    <p v-if="!user" class="text-body-secondary">Loading profile…</p>
    <div v-else class="card">
      <div class="card-body">
        <p class="text-body-secondary">
          Signed in as <code>{{ user.user_id }}</code>
        </p>
        <div class="row g-3">
          <div class="col-md-4">
            <label class="form-label small text-body-secondary mb-1" for="profile-email">
              Email
            </label>
            <BFormInput id="profile-email" v-model="user.email" type="email" autocomplete="off" />
          </div>
          <div class="col-md-4">
            <label class="form-label small text-body-secondary mb-1" for="profile-name">
              Full name
            </label>
            <BFormInput id="profile-name" v-model="user.full_name" />
          </div>
          <div class="col-md-4">
            <label class="form-label small text-body-secondary mb-1" for="profile-institution">
              Institution
            </label>
            <BFormInput id="profile-institution" v-model="user.institution" />
          </div>
        </div>

        <BFormCheckbox v-model="editPassword" class="mt-3">Change password</BFormCheckbox>
        <div v-if="editPassword" class="row g-3 mt-0">
          <div class="col-md-4">
            <label class="form-label small text-body-secondary mb-1" for="profile-password">
              New password
            </label>
            <BFormInput
              id="profile-password"
              v-model="user.password_plain"
              type="password"
              autocomplete="new-password" />
          </div>
        </div>

        <BButton variant="success" class="mt-4" title="Save profile" @click="save">
          <BIconFloppy />
          Save
        </BButton>
      </div>
    </div>
  </section>
</template>
