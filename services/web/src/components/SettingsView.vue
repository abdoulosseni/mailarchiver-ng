<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { getAppSettings, updateAppSettings, getStats, getHealth, getThroughput, getDlq, replayDlq, purgeDlq } from '../api.js'
import { t, locale } from '../i18n.js'

const emit = defineEmits(['expired'])

const stats = ref(null)
const health = ref(null)
const throughput = ref(null)
const dlq = ref(null)
let healthTimer = null

async function loadHealth() {
  try {
    health.value = await getHealth()
    throughput.value = await getThroughput()
    dlq.value = await getDlq()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
  }
}
async function doReplayDlq() {
  if (!confirm(t('settings.confirmReplay'))) return
  try {
    const r = await replayDlq()
    alert(t('settings.replayed', { n: r.replayed }))
    await loadHealth()
  } catch (e) {
    error.value = e.message
  }
}
async function doPurgeDlq() {
  if (!confirm(t('settings.confirmPurge'))) return
  try {
    await purgeDlq()
    await loadHealth()
  } catch (e) {
    error.value = e.message
  }
}
const retentionDays = ref(365)
const smtpd = ref({ host: '0.0.0.0', port: 2525, require_starttls: false, max_message_bytes: 52428800 })

function fmtBytes(n) {
  const u = locale.value === 'en' ? ['B', 'KB', 'MB', 'GB', 'TB'] : ['o', 'Ko', 'Mo', 'Go', 'To']
  if (!n) return '0 ' + u[0]
  let i = 0
  let v = n
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`
}
function fmtNum(n) {
  return (n ?? 0).toLocaleString(locale.value === 'en' ? 'en-US' : 'fr-FR')
}
function fmtDate(d) {
  return (d || '').slice(0, 16).replace('T', ' ') || '—'
}
const smtp = ref({ host: '', port: 587, username: '', starttls: true, from: '', password_set: false })
const smtpPassword = ref('') // laissé vide = inchangé
const message = ref('')
const error = ref('')

async function load() {
  error.value = ''
  try {
    const s = await getAppSettings()
    retentionDays.value = s.retention_days
    smtp.value = s.smtp
    if (s.smtpd) smtpd.value = s.smtpd
    smtpPassword.value = ''
    stats.value = await getStats()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

async function saveRetention() {
  await save({ retention_days: Number(retentionDays.value) }, t('settings.retentionSaved'))
}
async function saveSmtpd() {
  await save(
    {
      smtpd_host: smtpd.value.host,
      smtpd_port: Number(smtpd.value.port),
      smtpd_require_starttls: smtpd.value.require_starttls,
      smtpd_max_message_bytes: Number(smtpd.value.max_message_bytes),
    },
    t('settings.smtpdSaved'),
  )
}
async function saveSmtp() {
  const payload = {
    smtp_host: smtp.value.host,
    smtp_port: Number(smtp.value.port),
    smtp_username: smtp.value.username,
    smtp_starttls: smtp.value.starttls,
    smtp_from: smtp.value.from,
  }
  if (smtpPassword.value) payload.smtp_password = smtpPassword.value
  await save(payload, t('settings.smtpSaved'))
}

async function save(payload, ok) {
  error.value = ''
  message.value = ''
  try {
    const s = await updateAppSettings(payload)
    retentionDays.value = s.retention_days
    smtp.value = s.smtp
    if (s.smtpd) smtpd.value = s.smtpd
    smtpPassword.value = ''
    message.value = ok
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

onMounted(() => {
  load()
  loadHealth()
  healthTimer = setInterval(loadHealth, 10000) // rafraîchit la santé toutes les 10 s
})
onUnmounted(() => {
  if (healthTimer) clearInterval(healthTimer)
})
</script>

<template>
  <div class="layout">
  <div class="col-left">
  <section v-if="stats" class="card" style="max-width: 560px; margin-bottom: 14px">
    <h2>{{ t('settings.statsTitle') }}</h2>
    <div class="stats">
      <div class="stat"><span class="num">{{ fmtNum(stats.messages.count) }}</span><span class="lbl">{{ t('settings.statArchived') }}</span></div>
      <div class="stat"><span class="num">{{ fmtBytes(stats.messages.total_size_bytes) }}</span><span class="lbl">{{ t('settings.statVolume') }}</span></div>
      <div class="stat"><span class="num">{{ fmtNum(stats.attachments.stored) }}</span><span class="lbl">{{ t('settings.statDedup') }}</span></div>
      <div class="stat"><span class="num">{{ fmtNum(stats.attachments.references) }}</span><span class="lbl">{{ t('settings.statRefs') }}</span></div>
      <div class="stat"><span class="num">{{ fmtBytes(stats.attachments.stored_size_bytes) }}</span><span class="lbl">{{ t('settings.statStored') }}</span></div>
      <div class="stat"><span class="num">{{ fmtBytes(stats.attachments.dedup_saved_bytes) }}</span><span class="lbl">{{ t('settings.statSaved') }}</span></div>
      <div class="stat"><span class="num">{{ fmtNum(stats.indexed) }}</span><span class="lbl">{{ t('settings.statIndexed') }}</span></div>
      <div class="stat"><span class="num">{{ fmtNum(stats.accounts) }}</span><span class="lbl">{{ t('settings.statAccounts') }}</span></div>
      <div class="stat"><span class="num">{{ fmtNum(stats.fetch_sources) }}</span><span class="lbl">{{ t('settings.statSources') }}</span></div>
    </div>
    <p class="muted" style="margin-top: 10px">
      {{ t('settings.period', { a: fmtDate(stats.messages.date_oldest), b: fmtDate(stats.messages.date_newest), c: fmtDate(stats.messages.archived_oldest), d: fmtDate(stats.messages.archived_newest) }) }}
    </p>
  </section>

  <section class="card" style="max-width: 560px; margin-bottom: 14px">
    <h2>{{ t('settings.retentionTitle') }}</h2>
    <label>{{ t('settings.retentionLabel') }}</label>
    <input v-model="retentionDays" type="number" min="0" />
    <p class="muted" v-html="t('settings.retentionHelp')"></p>
    <div style="margin-top: 10px"><button @click="saveRetention">{{ t('settings.retentionSave') }}</button></div>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="message" class="muted">{{ message }}</p>
  </section>

  <section class="card" style="max-width: 560px; margin-bottom: 14px">
    <h2>{{ t('settings.smtpdTitle') }}</h2>
    <p class="muted" v-html="t('settings.smtpdIntro')"></p>
    <div class="filters">
      <div><label>{{ t('settings.smtpdBind') }}</label><input v-model="smtpd.host" placeholder="0.0.0.0" /></div>
      <div><label>{{ t('common.port') }}</label><input v-model="smtpd.port" type="number" /></div>
      <div><label>{{ t('settings.smtpdMaxSize') }}</label><input v-model="smtpd.max_message_bytes" type="number" /></div>
      <div>
        <label>{{ t('common.options') }}</label>
        <label class="opt"><input type="checkbox" v-model="smtpd.require_starttls" /> {{ t('settings.requireStarttls') }}</label>
      </div>
    </div>
    <div style="margin-top: 10px"><button @click="saveSmtpd">{{ t('settings.smtpdSave') }}</button></div>
    <p class="muted" style="margin-top: 8px" v-html="t('settings.smtpdNote')"></p>
  </section>

  <section class="card" style="max-width: 560px">
    <h2>{{ t('settings.smtpTitle') }}</h2>
    <p class="muted">{{ t('settings.smtpIntro') }}</p>
    <div class="filters">
      <div><label>{{ t('common.server') }}</label><input v-model="smtp.host" placeholder="smtp.example.com" /></div>
      <div><label>{{ t('common.port') }}</label><input v-model="smtp.port" type="number" /></div>
      <div><label>{{ t('settings.smtpUsername') }}</label><input v-model="smtp.username" /></div>
      <div>
        <label>{{ t('common.password') }} {{ smtp.password_set ? t('settings.smtpPasswordSet') : '' }}</label>
        <input v-model="smtpPassword" type="password" />
      </div>
      <div><label>{{ t('settings.smtpFrom') }}</label><input v-model="smtp.from" placeholder="archiver@example.com" /></div>
      <div>
        <label>{{ t('common.options') }}</label>
        <label class="opt"><input type="checkbox" v-model="smtp.starttls" /> {{ t('common.starttls') }}</label>
      </div>
    </div>
    <div style="margin-top: 10px"><button @click="saveSmtp">{{ t('settings.smtpSave') }}</button></div>

    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="message" class="muted">{{ message }}</p>
  </section>
  </div><!-- /col-left -->

  <aside class="col-right">
    <section class="card">
      <h2 style="display:flex; align-items:center; justify-content:space-between">
        {{ t('settings.systemTitle') }}
        <button class="link" @click="loadHealth">↻</button>
      </h2>
      <div v-if="health">
        <div class="hc-overall" :class="health.status">
          {{ health.status === 'ok' ? t('settings.systemAllOk') : t('settings.systemDegraded') }}
        </div>
        <ul class="hc-list">
          <li v-for="c in health.components" :key="c.name">
            <span class="dot" :class="c.status"></span>
            <span class="hc-name">{{ c.label }}</span>
            <span class="hc-meta">{{ c.status === 'ok' ? c.latency_ms + ' ms' : (c.detail || t('settings.systemUnavailable')) }}</span>
          </li>
        </ul>
        <p class="muted" style="margin-top:8px">{{ t('settings.systemAutoRefresh') }}</p>
      </div>
      <p v-else class="muted">{{ t('settings.systemChecking') }}</p>
    </section>

    <section class="card" style="margin-top: 14px">
      <h2>{{ t('settings.throughputTitle') }}</h2>
      <div v-if="throughput && throughput.available">
        <div class="rate">
          <span class="rate-val">{{ throughput.injection_rate }}</span>
          <span class="rate-lbl">{{ t('settings.throughputInjection') }}</span>
        </div>
        <div class="rate">
          <span class="rate-val">{{ throughput.processing_rate }}</span>
          <span class="rate-lbl">{{ t('settings.throughputProcessing') }}</span>
        </div>
        <ul class="hc-list" style="margin-top: 6px">
          <li><span class="hc-name">{{ t('settings.throughputBacklog') }}</span><span class="hc-meta">{{ throughput.backlog }}</span></li>
          <li><span class="hc-name">{{ t('settings.throughputWorkers') }}</span><span class="hc-meta">{{ throughput.consumers }}</span></li>
          <li><span class="hc-name">{{ t('settings.throughputDlq') }}</span><span class="hc-meta">{{ throughput.dead_letter }}</span></li>
        </ul>
        <ul class="notes">
          <li v-html="t('settings.throughputNote')"></li>
        </ul>
        <p class="muted" style="margin-top:8px">{{ t('settings.throughputAvg') }}</p>
      </div>
      <p v-else class="muted">{{ t('settings.throughputUnavailable') }}</p>
    </section>

    <section v-if="dlq" class="card" style="margin-top: 14px">
      <h2>{{ t('settings.dlqTitle') }}</h2>
      <p class="muted" v-if="!dlq.count">{{ t('settings.dlqEmpty') }}</p>
      <div v-else>
        <p v-html="t('settings.dlqCount', { n: dlq.count })"></p>
        <ul class="hc-list">
          <li v-for="(m, i) in dlq.preview" :key="i" style="display:block">
            <span class="hc-name">{{ m.subject }}</span>
            <span class="hc-meta" style="display:block; max-width:none">{{ m.from }}</span>
          </li>
        </ul>
        <div style="margin-top: 10px; display:flex; gap:10px">
          <button @click="doReplayDlq">{{ t('settings.dlqReplay') }}</button>
          <button class="ghost" @click="doPurgeDlq">{{ t('settings.dlqPurge') }}</button>
        </div>
      </div>
    </section>
  </aside>
  </div><!-- /layout -->
</template>

<style scoped>
h3 { font-size: 13px; margin: 4px 0 8px; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.stat { border: 1px solid var(--border); border-radius: 4px; padding: 10px; text-align: center; }
.stat .num { display: block; font-size: 18px; font-weight: 600; }
.stat .lbl { display: block; font-size: 11px; color: var(--muted); margin-top: 2px; }
hr { border: 0; border-top: 1px solid var(--border); margin: 18px 0; }
.opt { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--text); }
.opt input { width: auto; }

.layout { display: flex; gap: 14px; align-items: flex-start; flex-wrap: wrap; }
.col-left { flex: 1; min-width: 340px; }
.col-right { width: 300px; flex-shrink: 0; }

.hc-overall { font-size: 12px; padding: 6px 8px; border-radius: 4px; margin-bottom: 10px; text-align: center; }
.hc-overall.ok { background: rgba(108,192,108,0.15); color: #4caf50; }
.hc-overall.degraded { background: rgba(224,108,108,0.15); color: #e06c6c; }
.hc-list { list-style: none; margin: 0; padding: 0; }
.hc-list li { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.dot.ok { background: #4caf50; box-shadow: 0 0 4px #4caf50; }
.dot.down { background: #e06c6c; box-shadow: 0 0 4px #e06c6c; }
.hc-name { flex: 1; }
.hc-meta { font-size: 11px; color: var(--muted); max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rate { text-align: center; padding: 8px 0; }
.rate-val { display: block; font-size: 26px; font-weight: 700; color: var(--accent); }
.rate-lbl { display: block; font-size: 11px; color: var(--muted); }
.notes { margin: 8px 0 0; padding-left: 16px; }
.notes li { font-size: 11px; color: var(--muted); line-height: 1.4; }
</style>
