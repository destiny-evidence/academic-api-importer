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
  <article class="login">
    <h1>Log in</h1>
    <p>Sign in with your account email and password.</p>
    <form @submit.prevent="submit">
      <label>
        Email
        <input v-model="email" type="email" autocomplete="email" spellcheck="false" required />
      </label>
      <label>
        Password
        <input v-model="password" type="password" autocomplete="current-password" required />
      </label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button type="submit" :disabled="submitting || !email.trim() || !password">
        {{ submitting ? 'Signing in…' : 'Sign in' }}
      </button>
    </form>
  </article>
</template>

<style scoped>
/* Pico renders <article> as a card; only the width/centering is app-specific. */
.login {
  max-width: 24rem;
  margin: 4rem auto;
}

.error {
  color: var(--pico-form-element-invalid-border-color);
}
</style>
