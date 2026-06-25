<script setup>
import { ref } from 'vue'
import { login, setSession } from '../api.js'

const emit = defineEmits(['logged-in'])

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    const data = await login(username.value, password.value)
    setSession(data.token, data.username + (data.is_admin ? ' (admin)' : ''), data.is_admin)
    emit('logged-in')
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section class="card">
    <h2>Connexion</h2>
    <label>Identifiant</label>
    <input v-model="username" autocomplete="username" />
    <label>Mot de passe</label>
    <input v-model="password" type="password" autocomplete="current-password" @keyup.enter="submit" />
    <div style="margin-top: 16px">
      <button :disabled="loading" @click="submit">{{ loading ? '…' : 'Se connecter' }}</button>
    </div>
    <p v-if="error" class="err">{{ error }}</p>
  </section>
</template>
