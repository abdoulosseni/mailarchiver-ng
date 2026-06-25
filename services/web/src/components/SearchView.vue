<script setup>
import { ref, onMounted } from 'vue'
import { searchAdvanced, fetchEml, getMessage } from '../api.js'
import MessageModal from './MessageModal.vue'

const emit = defineEmits(['expired'])

const filters = ref({
  text: '',
  from_: '',
  to: '',
  participant: '',
  subject: '',
  date_from: '',
  date_to: '',
})
const results = ref([])
const info = ref('')
const selected = ref(null) // mail en cours de consultation
const total = ref(0)
const totalEstimated = ref(false)
const pageSize = 50
// Pagination par curseur (search_after) : cursors[i] = curseur de la page i.
const cursors = ref([null])
const pageIndex = ref(0)

function cleanFilters() {
  const out = {}
  for (const [k, v] of Object.entries(filters.value)) {
    if (v !== '' && v !== false && v !== null) out[k] = v
  }
  return out
}

async function run() {
  cursors.value = [null] // nouvelle recherche → curseur initial
  pageIndex.value = 0
  await load()
}

async function load() {
  info.value = 'Recherche…'
  results.value = []
  try {
    const data = await searchAdvanced(cleanFilters(), cursors.value[pageIndex.value], pageSize)
    results.value = data.results
    total.value = data.total
    totalEstimated.value = data.totalEstimated
    // Mémorise le curseur de la page suivante (s'il y a probablement une suite).
    if (
      pageIndex.value === cursors.value.length - 1 &&
      data.nextSearchAfter &&
      data.results.length === pageSize
    ) {
      cursors.value.push(data.nextSearchAfter)
    }
    if (total.value === 0) {
      info.value = 'Aucun résultat'
    } else {
      const start = pageIndex.value * pageSize + 1
      const end = pageIndex.value * pageSize + results.value.length
      const tot = total.value + (totalEstimated.value ? '+' : '')
      info.value = `${tot} résultat(s) — affichage ${start}–${end}`
    }
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else info.value = 'Erreur lors de la recherche'
  }
}

const hasNext = () => pageIndex.value < cursors.value.length - 1
async function prevPage() {
  if (pageIndex.value > 0) {
    pageIndex.value--
    await load()
  }
}
async function nextPage() {
  if (hasNext()) {
    pageIndex.value++
    await load()
  }
}

async function openMessage(m) {
  try {
    selected.value = await getMessage(m.id)
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else alert(e.message)
  }
}
function closeMessage() {
  selected.value = null
}

async function exportEml(m) {
  try {
    const { blob, integrity } = await fetchEml(m.id)
    if (integrity && integrity !== 'valid') {
      alert('⚠️ Signature INVALIDE — archive potentiellement altérée !')
    }
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `message-${m.id}.eml`
    a.click()
    URL.revokeObjectURL(a.href)
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else alert(e.message)
  }
}

function fmtDate(d) {
  return (d || '').slice(0, 16).replace('T', ' ')
}

// Liste tous les mails accessibles dès l'ouverture, sans clic sur « Rechercher ».
onMounted(run)
</script>

<template>
  <section class="card">
    <h2>Recherche avancée</h2>
    <div class="filters">
      <div><label>Texte (sujet / corps)</label><input v-model="filters.text" @keyup.enter="run" /></div>
      <div><label>Expéditeur</label><input v-model="filters.from_" placeholder="alice@corp.com" @keyup.enter="run" /></div>
      <div><label>Destinataire</label><input v-model="filters.to" @keyup.enter="run" /></div>
      <div><label>Expéditeur ou destinataire</label><input v-model="filters.participant" placeholder="alice@corp.com" @keyup.enter="run" /></div>
      <div><label>Sujet exact</label><input v-model="filters.subject" @keyup.enter="run" /></div>
      <div><label>Date début</label><input v-model="filters.date_from" type="date" /></div>
      <div><label>Date fin</label><input v-model="filters.date_to" type="date" /></div>
    </div>
    <div style="margin-top: 14px"><button @click="run">Rechercher</button></div>

    <p class="muted" style="margin-top: 14px">{{ info }}</p>

    <table v-if="results.length">
      <thead>
        <tr><th>Date du mail</th><th>Archivé le</th><th>De</th><th>À</th><th>Sujet</th><th>PJ</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="m in results" :key="m.id">
          <td>{{ fmtDate(m.date) }}</td>
          <td>{{ fmtDate(m.archived_at) }}</td>
          <td>{{ m.from_addr }}</td>
          <td>{{ (m.to_addrs || []).join(', ') }}</td>
          <td>{{ m.subject }}</td>
          <td><span v-if="m.has_attachment" class="pill">{{ (m.attachment_names || []).length }}</span></td>
          <td>
            <button class="link" @click="openMessage(m)">Consulter</button>
            <button class="link sep" @click="exportEml(m)">Télécharger EML</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="pageIndex > 0 || hasNext()" class="pager">
      <button class="ghost" :disabled="pageIndex === 0" @click="prevPage">← Précédent</button>
      <span class="muted">Page {{ pageIndex + 1 }}</span>
      <button class="ghost" :disabled="!hasNext()" @click="nextPage">Suivant →</button>
    </div>
  </section>

  <MessageModal v-if="selected" :message="selected" @close="closeMessage" />
</template>

<style scoped>
.sep { margin-left: 12px; }
.pager { display: flex; align-items: center; gap: 12px; margin-top: 16px; flex-wrap: wrap; }
.goto { width: 70px; }
</style>
