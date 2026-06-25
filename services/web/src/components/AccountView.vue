<script setup>
import { ref } from 'vue'
import { changeOwnPassword } from '../api.js'
import { t } from '../i18n.js'

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
    error.value = t('account.errNewRequired')
    return
  }
  if (newPassword.value !== confirm.value) {
    error.value = t('account.errMismatch')
    return
  }
  try {
    await changeOwnPassword(oldPassword.value, newPassword.value)
    message.value = t('account.ok')
    oldPassword.value = newPassword.value = confirm.value = ''
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}
</script>

<template>
  <section class="card" style="max-width: 420px">
    <h2>{{ t('account.title') }}</h2>
    <label>{{ t('account.current') }}</label>
    <input v-model="oldPassword" type="password" autocomplete="current-password" />
    <label>{{ t('account.new') }}</label>
    <input v-model="newPassword" type="password" autocomplete="new-password" />
    <label>{{ t('account.confirm') }}</label>
    <input v-model="confirm" type="password" autocomplete="new-password" @keyup.enter="submit" />
    <div style="margin-top: 16px"><button @click="submit">{{ t('account.submit') }}</button></div>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="message" class="muted">{{ message }}</p>
  </section>
</template>
