<script setup>
import { ref } from 'vue'
import { fetchEml, setLegalHold, isAdmin } from '../api.js'

const props = defineProps({ message: { type: Object, required: true } })
defineEmits(['close'])

const admin = isAdmin()
const held = ref(!!props.message.legal_hold)
async function toggleHold() {
  try {
    const r = await setLegalHold(props.message.id, !held.value)
    held.value = r.legal_hold
  } catch (e) {
    alert(e.message)
  }
}

async function exportEml() {
  try {
    const { blob, integrity } = await fetchEml(props.message.id)
    if (integrity && integrity !== 'valid') {
      alert('⚠️ Signature INVALIDE — archive potentiellement altérée !')
    }
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `message-${props.message.id}.eml`
    a.click()
    URL.revokeObjectURL(a.href)
  } catch (e) {
    alert(e.message)
  }
}
</script>

<template>
  <div class="overlay" @click.self="$emit('close')">
    <div class="modal card">
      <div class="modal-head">
        <h2>{{ message.subject || '(sans sujet)' }}</h2>
        <button class="ghost" @click="$emit('close')">Fermer</button>
      </div>
      <div class="meta">
        <div><span class="muted">De :</span> {{ message.from }}</div>
        <div><span class="muted">À :</span> {{ (message.to || []).join(', ') }}</div>
        <div v-if="message.cc && message.cc.length"><span class="muted">Cc :</span> {{ message.cc.join(', ') }}</div>
        <div><span class="muted">Date du mail :</span> {{ message.date }}</div>
        <div v-if="message.archived_at"><span class="muted">Archivé le :</span> {{ message.archived_at }}</div>
        <div>
          <span class="muted">Intégrité :</span>
          <span :class="message.integrity_ok ? 'ok' : 'err'">
            {{ message.integrity_ok ? 'signature valide ✓' : 'signature INVALIDE ✗' }}
          </span>
        </div>
      </div>

      <div v-if="message.attachments && message.attachments.length" class="attachments">
        <span class="muted">Pièces jointes :</span>
        <span v-for="(a, i) in message.attachments" :key="i" class="pill att">
          {{ a.filename }} ({{ Math.round(a.size / 1024) }} Ko)
        </span>
      </div>

      <details v-if="message.headers && message.headers.length" class="headers">
        <summary>Toutes les en-têtes ({{ message.headers.length }})</summary>
        <table class="hdr-table">
          <tbody>
            <tr v-for="(h, i) in message.headers" :key="i">
              <td class="hdr-name">{{ h.name }}</td>
              <td class="hdr-value">{{ h.value }}</td>
            </tr>
          </tbody>
        </table>
      </details>

      <pre class="body">{{ message.body || '(corps vide)' }}</pre>
      <p v-if="message.has_html" class="muted">
        Ce mail contient une version HTML ; le corps texte est affiché. Téléchargez le .eml pour la version d'origine.
      </p>

      <div class="modal-actions">
        <button @click="exportEml">Télécharger EML</button>
        <button v-if="admin" class="ghost" @click="toggleHold">
          {{ held ? '🔒 Lever la conservation légale' : 'Mettre en conservation légale' }}
        </button>
        <span v-if="held" class="hold">⚖️ Conservation légale active (exclu de la purge)</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 40px 16px;
  overflow: auto;
}
.modal { max-width: 760px; width: 100%; }
.modal-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.modal-head h2 { margin: 0; }
.meta { margin: 14px 0; font-size: 14px; line-height: 1.6; }
.attachments { margin-bottom: 12px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.att { color: var(--text); }
.headers { margin-bottom: 12px; }
.headers summary { cursor: pointer; color: var(--accent); font-size: 13px; }
.hdr-table { margin-top: 8px; font-size: 12px; }
.hdr-table td { padding: 4px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
.hdr-name { color: var(--muted); white-space: nowrap; font-family: ui-monospace, monospace; }
.hdr-value { font-family: ui-monospace, monospace; word-break: break-all; }
.body {
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--input-bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 14px;
  max-height: 50vh;
  overflow: auto;
  font-family: ui-monospace, monospace;
  font-size: 13px;
}
.ok { color: #6cc06c; }
.modal-actions { margin-top: 14px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.hold { color: var(--accent); font-size: 13px; }
</style>
