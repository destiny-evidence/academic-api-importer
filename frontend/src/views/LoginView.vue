<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useSessionStore } from '@/stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

const email = ref('')
const password = ref('')
const error = ref('')
const submitting = ref(false)

async function submit() {
  error.value = ''
  submitting.value = true
  try {
    await session.login(email.value.trim(), password.value)
    router.replace(
      typeof route.query.redirect === 'string' ? route.query.redirect : { name: 'queries' },
    )
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Login failed'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login card shadow-sm">
    <div class="card-body">
      <h1 class="h4">Log in</h1>
      <p class="text-body-secondary">Sign in with your account email and password.</p>
      <form @submit.prevent="submit">
        <div class="mb-3">
          <label for="email" class="form-label">Email</label>
          <input
            id="email"
            v-model="email"
            class="form-control"
            type="email"
            autocomplete="email"
            spellcheck="false"
            required
          />
        </div>
        <div class="mb-3">
          <label for="password" class="form-label">Password</label>
          <input
            id="password"
            v-model="password"
            class="form-control"
            type="password"
            autocomplete="current-password"
            required
          />
        </div>
        <p v-if="error" class="text-danger" role="alert">{{ error }}</p>
        <button
          type="submit"
          class="btn btn-primary w-100"
          :disabled="submitting || !email.trim() || !password"
        >
          {{ submitting ? 'Signing in…' : 'Sign in' }}
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
/* Only the width/centering of the login card is app-specific. */
.login {
  max-width: 24rem;
  margin: 4rem auto;
}
</style>
