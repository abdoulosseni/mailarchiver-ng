<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { getToken, getMessage } from '../api.js'
import MessageModal from './MessageModal.vue'

const emit = defineEmits(['expired'])

const live = ref([])
const connected = ref(false)
const selected = ref(null)
let es = null

function start() {
  es = new EventSource('/events/stream?token=' + encodeURIComponent(getToken()))
  es.onopen = () => (connected.value = true)
  es.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data)
      live.value.unshift(m) // les plus récents en haut
      if (live.value.length > 200) live.value.pop()
    } catch (_) {}
  }
  es.onerror = () => (connected.value = false) // EventSource se reconnecte seul
}

async function openMessage(m) {
  try {
    selected.value = await getMessage(m.id)
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else alert(e.message)
  }
}

function fmtDate(d) {
  return (d || '').slice(0, 16).replace('T', ' ')
}

onMounted(start)
onUnmounted(() => es && es.close())
</script>

<template>
  <section class="card">
    <h2>
      Mails en temps réel
      <span :class="connected ? 'ok' : 'muted'" style="font-size: 13px; font-weight: 400">
        {{ connected ? '● connecté' : '○ connexion…' }}
      </span>
    </h2>
    <p class="muted">Les nouveaux mails archivés s'affichent ici automatiquement, sans rafraîchir.</p>

    <table v-if="live.length">
      <thead>
        <tr><th>Reçu</th><th>De</th><th>À</th><th>Sujet</th><th>PJ</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="m in live" :key="m.id">
          <td>{{ fmtDate(m.date) }}</td>
          <td>{{ m.from_addr }}</td>
          <td>{{ (m.to_addrs || []).join(', ') }}</td>
          <td>{{ m.subject }}</td>
          <td><span v-if="m.has_attachment" class="pill">{{ (m.attachment_names || []).length }}</span></td>
          <td><button class="link" @click="openMessage(m)">Consulter</button></td>
        </tr>
      </tbody>
    </table>
    <p v-else class="muted" style="margin-top: 16px">En attente de nouveaux mails…</p>
  </section>

  <MessageModal v-if="selected" :message="selected" @close="selected = null" />
</template>

<style scoped>
.ok { color: #6cc06c; }
</style>
