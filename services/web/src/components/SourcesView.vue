<script setup>
import { ref, onMounted } from 'vue'
import { listFetchSources, createFetchSource, deleteFetchSource, runFetchSource } from '../api.js'
import { t } from '../i18n.js'

const emit = defineEmits(['expired'])

// Hôte = adresse utilisée pour accéder à l'interface (IP du host) ; port = port
// publié par Docker pour la passerelle SMTP (mapping docker-compose, 2525).
const smtpHost = window.location.hostname || 'IP-serveur'
const smtpPort = 2525
const sources = ref([])
const error = ref('')
const blank = () => ({
  name: '',
  protocol: 'imap',
  host: '',
  port: 993,
  username: '',
  password: '',
  use_ssl: true,
  folder: 'INBOX',
  interval_minutes: 15,
  delete_after: false,
})
const form = ref(blank())

async function refresh() {
  error.value = ''
  try {
    sources.value = await listFetchSources()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

async function submit() {
  error.value = ''
  try {
    await createFetchSource({ ...form.value, port: Number(form.value.port) })
    form.value = blank()
    await refresh()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

async function runNow(s) {
  error.value = ''
  try {
    const r = await runFetchSource(s.id)
    alert(t('sources.runResult', { name: s.name, status: r.status }))
    await refresh()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

async function remove(s) {
  if (!confirm(t('sources.confirmDelete', { name: s.name }))) return
  try {
    await deleteFetchSource(s.id)
    await refresh()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

// Ajuste le port par défaut selon protocole/SSL
function onProtoChange() {
  if (form.value.protocol === 'imap') form.value.port = form.value.use_ssl ? 993 : 143
  else form.value.port = form.value.use_ssl ? 995 : 110
}

onMounted(refresh)
</script>

<template>
  <section class="card" style="margin-bottom: 14px">
    <h2>{{ t('sources.smtpTitle') }}</h2>
    <p class="muted" v-html="t('sources.smtpIntro1')"></p>
    <p class="muted" v-html="t('sources.smtpIntro2', { host: smtpHost, port: smtpPort })"></p>

    <h3>{{ t('sources.postfixTitle') }}</h3>
    <p class="muted" v-html="t('sources.step1')"></p>
    <pre class="cfg">{{ t('sources.cfgMain') }}</pre>

    <p class="muted" v-html="t('sources.step2')"></p>
    <pre class="cfg">mailarchiver.invalid    smtp:[{{ smtpHost }}]:{{ smtpPort }}</pre>

    <p class="muted">{{ t('sources.step3') }}</p>
    <pre class="cfg">postmap /etc/postfix/transport
systemctl reload postfix</pre>

    <p class="muted" v-html="t('sources.variant')"></p>
  </section>

  <section class="card">
    <h2>{{ t('sources.imapTitle') }}</h2>
    <p class="muted">{{ t('sources.imapIntro') }}</p>

    <div class="filters">
      <div><label>{{ t('sources.fName') }}</label><input v-model="form.name" :placeholder="t('sources.fNamePlaceholder')" /></div>
      <div>
        <label>{{ t('sources.fProtocol') }}</label>
        <select v-model="form.protocol" @change="onProtoChange">
          <option value="imap">IMAP</option>
          <option value="pop3">POP3</option>
        </select>
      </div>
      <div><label>{{ t('common.server') }}</label><input v-model="form.host" placeholder="mail.example.com" /></div>
      <div><label>{{ t('common.port') }}</label><input v-model="form.port" type="number" /></div>
      <div><label>{{ t('common.username') }}</label><input v-model="form.username" /></div>
      <div><label>{{ t('common.password') }}</label><input v-model="form.password" type="password" /></div>
      <div v-if="form.protocol === 'imap'"><label>{{ t('common.folder') }}</label><input v-model="form.folder" /></div>
      <div><label>{{ t('sources.fInterval') }}</label><input v-model="form.interval_minutes" type="number" /></div>
      <div>
        <label>{{ t('common.options') }}</label>
        <label class="opt"><input type="checkbox" v-model="form.use_ssl" @change="onProtoChange" /> {{ t('common.ssltls') }}</label>
        <label class="opt"><input type="checkbox" v-model="form.delete_after" /> {{ t('sources.optDeleteAfter') }}</label>
      </div>
    </div>
    <div style="margin-top: 14px"><button @click="submit">{{ t('sources.add') }}</button></div>
    <p v-if="error" class="err">{{ error }}</p>

    <table v-if="sources.length">
      <thead>
        <tr><th>{{ t('sources.thName') }}</th><th>{{ t('sources.thProtocol') }}</th><th>{{ t('sources.thServer') }}</th><th>{{ t('sources.thUsername') }}</th><th>{{ t('sources.thInterval') }}</th><th>{{ t('sources.thLastRun') }}</th><th>{{ t('sources.thStatus') }}</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="s in sources" :key="s.id">
          <td>{{ s.name }}</td>
          <td>{{ s.protocol.toUpperCase() }}{{ s.use_ssl ? 'S' : '' }}</td>
          <td>{{ s.host }}:{{ s.port }}</td>
          <td>{{ s.username }}</td>
          <td>{{ s.interval_minutes }} {{ t('sources.minSuffix') }}</td>
          <td>{{ (s.last_run || '').slice(0, 16).replace('T', ' ') || t('common.dash') }}</td>
          <td>{{ s.last_status || t('common.dash') }}</td>
          <td>
            <button class="link" @click="runNow(s)">{{ t('sources.runNow') }}</button>
            <button class="link sep" @click="remove(s)">{{ t('common.delete') }}</button>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.sep { margin-left: 12px; }
.opt { display: inline-flex; align-items: center; gap: 4px; margin-right: 12px; font-size: 12px; color: var(--text); }
.opt input { width: auto; }
h3 { font-size: 13px; margin: 14px 0 6px; }
.cfg {
  background: var(--input-bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 10px 12px;
  font-family: ui-monospace, monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 4px 0 10px;
}
select { width: 100%; padding: 5px 8px; border-radius: 3px; border: 1px solid var(--border); background: var(--input-bg); color: var(--text); font-size: 13px; }
</style>
