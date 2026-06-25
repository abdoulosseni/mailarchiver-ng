<script setup>
import { ref } from 'vue'
import { changeOwnPassword } from '../api.js'

const emit = defineEmits(['expired'])

const oldPassword = ref('')
const newPassword = ref('')
const confirm = ref('')
const error = ref('')
const message = ref('')

async function submit() {
  error.value = ''
  message.value = ''
  if (!newPassword.value) {
    error.value = 'Nouveau mot de passe requis'
    return
  }
  if (newPassword.value !== confirm.value) {
    error.value = 'La confirmation ne correspond pas'
    return
  }
  try {
    await changeOwnPassword(oldPassword.value, newPassword.value)
    message.value = 'Mot de passe modifié.'
    oldPassword.value = newPassword.value = confirm.value = ''
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}
</script>

<template>
  <section class="card" style="max-width: 420px">
    <h2>Mon mot de passe</h2>
    <label>Mot de passe actuel</label>
    <input v-model="oldPassword" type="password" autocomplete="current-password" />
    <label>Nouveau mot de passe</label>
    <input v-model="newPassword" type="password" autocomplete="new-password" />
    <label>Confirmer le nouveau mot de passe</label>
    <input v-model="confirm" type="password" autocomplete="new-password" @keyup.enter="submit" />
    <div style="margin-top: 16px"><button @click="submit">Modifier</button></div>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="message" class="muted">{{ message }}</p>
  </section>
</template>
