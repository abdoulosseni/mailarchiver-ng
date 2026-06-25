<script setup>
import { ref, onMounted } from 'vue'
import { searchAdvanced, fetchEml, getMessage } from '../api.js'
import { t } from '../i18n.js'
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
  info.value = t('search.searching')
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
      info.value = t('search.noResults')
    } else {
      const start = pageIndex.value * pageSize + 1
      const end = pageIndex.value * pageSize + results.value.length
      const tot = total.value + (totalEstimated.value ? '+' : '')
      info.value = t('search.resultsInfo', { tot, start, end })
    }
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else info.value = t('search.error')
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
      alert(t('message.sigInvalidAlert'))
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
    <h2>{{ t('search.title') }}</h2>
    <div class="filters">
      <div><label>{{ t('search.fText') }}</label><input v-model="filters.text" @keyup.enter="run" /></div>
      <div><label>{{ t('search.fFrom') }}</label><input v-model="filters.from_" placeholder="alice@corp.com" @keyup.enter="run" /></div>
      <div><label>{{ t('search.fTo') }}</label><input v-model="filters.to" @keyup.enter="run" /></div>
      <div><label>{{ t('search.fParticipant') }}</label><input v-model="filters.participant" placeholder="alice@corp.com" @keyup.enter="run" /></div>
      <div><label>{{ t('search.fSubject') }}</label><input v-model="filters.subject" @keyup.enter="run" /></div>
      <div><label>{{ t('search.fDateFrom') }}</label><input v-model="filters.date_from" type="date" /></div>
      <div><label>{{ t('search.fDateTo') }}</label><input v-model="filters.date_to" type="date" /></div>
    </div>
    <div style="margin-top: 14px"><button @click="run">{{ t('search.search') }}</button></div>

    <p class="muted" style="margin-top: 14px">{{ info }}</p>

    <table v-if="results.length">
      <thead>
        <tr><th>{{ t('search.thDate') }}</th><th>{{ t('search.thArchived') }}</th><th>{{ t('search.thFrom') }}</th><th>{{ t('search.thTo') }}</th><th>{{ t('search.thSubject') }}</th><th>{{ t('search.thAtt') }}</th><th></th></tr>
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
            <button class="link" @click="openMessage(m)">{{ t('common.view') }}</button>
            <button class="link sep" @click="exportEml(m)">{{ t('common.downloadEml') }}</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="pageIndex > 0 || hasNext()" class="pager">
      <button class="ghost" :disabled="pageIndex === 0" @click="prevPage">{{ t('search.prev') }}</button>
      <span class="muted">{{ t('search.page', { n: pageIndex + 1 }) }}</span>
      <button class="ghost" :disabled="!hasNext()" @click="nextPage">{{ t('search.next') }}</button>
    </div>
  </section>

  <MessageModal v-if="selected" :message="selected" @close="closeMessage" />
</template>

<style scoped>
.sep { margin-left: 12px; }
.pager { display: flex; align-items: center; gap: 12px; margin-top: 16px; flex-wrap: wrap; }
.goto { width: 70px; }
</style>
