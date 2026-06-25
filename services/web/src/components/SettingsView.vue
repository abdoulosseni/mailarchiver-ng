<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { getAppSettings, updateAppSettings, getStats, getHealth, getThroughput, getDlq, replayDlq, purgeDlq } from '../api.js'
import { t } from '../i18n.js'

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
  if (!n) return '0 o'
  const u = ['o', 'Ko', 'Mo', 'Go', 'To']
  let i = 0
  let v = n
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`
}
function fmtNum(n) {
  return (n ?? 0).toLocaleString('fr-FR')
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
  await save({ retention_days: Number(retentionDays.value) }, 'Conservation enregistrée.')
}
async function saveSmtpd() {
  await save(
    {
      smtpd_host: smtpd.value.host,
      smtpd_port: Number(smtpd.value.port),
      smtpd_require_starttls: smtpd.value.require_starttls,
      smtpd_max_message_bytes: Number(smtpd.value.max_message_bytes),
    },
    'Serveur SMTPD enregistré (redémarrer le service smtp-gateway pour appliquer).',
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
  await save(payload, 'Relais SMTP enregistré.')
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
    <h2>Statistiques d'archivage</h2>
    <div class="stats">
      <div class="stat"><span class="num">{{ fmtNum(stats.messages.count) }}</span><span class="lbl">mails archivés</span></div>
      <div class="stat"><span class="num">{{ fmtBytes(stats.messages.total_size_bytes) }}</span><span class="lbl">volume des mails</span></div>
      <div class="stat"><span class="num">{{ fmtNum(stats.attachments.stored) }}</span><span class="lbl">PJ stockées (dédup.)</span></div>
      <div class="stat"><span class="num">{{ fmtNum(stats.attachments.references) }}</span><span class="lbl">références de PJ</span></div>
      <div class="stat"><span class="num">{{ fmtBytes(stats.attachments.stored_size_bytes) }}</span><span class="lbl">PJ stockées</span></div>
      <div class="stat"><span class="num">{{ fmtBytes(stats.attachments.dedup_saved_bytes) }}</span><span class="lbl">économisé par dédup.</span></div>
      <div class="stat"><span class="num">{{ fmtNum(stats.indexed) }}</span><span class="lbl">indexés (recherche)</span></div>
      <div class="stat"><span class="num">{{ fmtNum(stats.accounts) }}</span><span class="lbl">comptes</span></div>
      <div class="stat"><span class="num">{{ fmtNum(stats.fetch_sources) }}</span><span class="lbl">sources IMAP/POP</span></div>
    </div>
    <p class="muted" style="margin-top: 10px">
      Période des mails : {{ fmtDate(stats.messages.date_oldest) }} → {{ fmtDate(stats.messages.date_newest) }} ·
      Archivés du {{ fmtDate(stats.messages.archived_oldest) }} au {{ fmtDate(stats.messages.archived_newest) }}
    </p>
  </section>

  <section class="card" style="max-width: 560px; margin-bottom: 14px">
    <h2>Politique de conservation des mails</h2>
    <label>Durée de conservation (en jours)</label>
    <input v-model="retentionDays" type="number" min="0" />
    <p class="muted">
      Mails archivés depuis plus de cette durée = purgés automatiquement.
      Défaut : <strong>365 jours (1 an)</strong>. <code>0</code> = illimité.
    </p>
    <div style="margin-top: 10px"><button @click="saveRetention">Enregistrer la conservation</button></div>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="message" class="muted">{{ message }}</p>
  </section>

  <section class="card" style="max-width: 560px; margin-bottom: 14px">
    <h2>Serveur SMTP entrant (SMTPD)</h2>
    <p class="muted">Passerelle qui reçoit les mails à archiver (journaling). Voir l'onglet <em>Sources</em> pour la configuration côté MTA.</p>
    <div class="filters">
      <div><label>Adresse de bind (IP)</label><input v-model="smtpd.host" placeholder="0.0.0.0" /></div>
      <div><label>Port</label><input v-model="smtpd.port" type="number" /></div>
      <div><label>Taille max. par mail (octets)</label><input v-model="smtpd.max_message_bytes" type="number" /></div>
      <div>
        <label>Options</label>
        <label class="opt"><input type="checkbox" v-model="smtpd.require_starttls" /> Exiger STARTTLS</label>
      </div>
    </div>
    <div style="margin-top: 10px"><button @click="saveSmtpd">Enregistrer le SMTPD</button></div>
    <p class="muted" style="margin-top: 8px">
      ⚠ Prend effet au <strong>redémarrage du service smtp-gateway</strong> (un socket d'écoute ne se reconfigure pas à chaud).
      En déploiement Docker, le <strong>port publié</strong> doit correspondre (mapping <code>docker-compose</code>).
    </p>
  </section>

  <section class="card" style="max-width: 560px">
    <h2>Relais SMTP (transfert vers les auditeurs)</h2>
    <p class="muted">Utilisé pour transférer les mails du périmètre vers l'adresse d'un auditeur (page Comptes).</p>
    <div class="filters">
      <div><label>Serveur</label><input v-model="smtp.host" placeholder="smtp.example.com" /></div>
      <div><label>Port</label><input v-model="smtp.port" type="number" /></div>
      <div><label>Identifiant (optionnel)</label><input v-model="smtp.username" /></div>
      <div>
        <label>Mot de passe {{ smtp.password_set ? '(défini — laisser vide pour conserver)' : '' }}</label>
        <input v-model="smtpPassword" type="password" />
      </div>
      <div><label>Adresse d'expédition (From)</label><input v-model="smtp.from" placeholder="archiver@example.com" /></div>
      <div>
        <label>Options</label>
        <label class="opt"><input type="checkbox" v-model="smtp.starttls" /> STARTTLS</label>
      </div>
    </div>
    <div style="margin-top: 10px"><button @click="saveSmtp">Enregistrer le relais SMTP</button></div>

    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="message" class="muted">{{ message }}</p>
  </section>
  </div><!-- /col-left -->

  <aside class="col-right">
    <section class="card">
      <h2 style="display:flex; align-items:center; justify-content:space-between">
        État du système
        <button class="link" @click="loadHealth">↻</button>
      </h2>
      <div v-if="health">
        <div class="hc-overall" :class="health.status">
          {{ health.status === 'ok' ? 'Tous les composants opérationnels' : 'Dégradé' }}
        </div>
        <ul class="hc-list">
          <li v-for="c in health.components" :key="c.name">
            <span class="dot" :class="c.status"></span>
            <span class="hc-name">{{ c.label }}</span>
            <span class="hc-meta">{{ c.status === 'ok' ? c.latency_ms + ' ms' : (c.detail || 'indisponible') }}</span>
          </li>
        </ul>
        <p class="muted" style="margin-top:8px">Actualisé automatiquement toutes les 10 s.</p>
      </div>
      <p v-else class="muted">Vérification…</p>
    </section>

    <section class="card" style="margin-top: 14px">
      <h2>Débit (temps réel)</h2>
      <div v-if="throughput && throughput.available">
        <div class="rate">
          <span class="rate-val">{{ throughput.injection_rate }}</span>
          <span class="rate-lbl">injection (msg/s)</span>
        </div>
        <div class="rate">
          <span class="rate-val">{{ throughput.processing_rate }}</span>
          <span class="rate-lbl">traitement / archivage (msg/s)</span>
        </div>
        <ul class="hc-list" style="margin-top: 6px">
          <li><span class="hc-name">En file (backlog)</span><span class="hc-meta">{{ throughput.backlog }}</span></li>
          <li><span class="hc-name">Workers connectés</span><span class="hc-meta">{{ throughput.consumers }}</span></li>
          <li><span class="hc-name">En quarantaine (DLQ)</span><span class="hc-meta">{{ throughput.dead_letter }}</span></li>
        </ul>
        <ul class="notes">
          <li><strong>Quarantaine (DLQ, <em>Dead Letter Queue</em>)</strong> : mails dont le traitement a échoué après plusieurs tentatives. Ils y sont mis de côté (jamais perdus) pour inspection/rejeu, au lieu de boucler indéfiniment. Une valeur &gt; 0 signale un incident à examiner.</li>
        </ul>
        <p class="muted" style="margin-top:8px">Taux moyennés par RabbitMQ ; actualisé toutes les 10 s.</p>
      </div>
      <p v-else class="muted">Métriques indisponibles.</p>
    </section>

    <section v-if="dlq" class="card" style="margin-top: 14px">
      <h2>Quarantaine (DLQ)</h2>
      <p class="muted" v-if="!dlq.count">Aucun mail en quarantaine ✓</p>
      <div v-else>
        <p><strong>{{ dlq.count }}</strong> mail(s) en quarantaine.</p>
        <ul class="hc-list">
          <li v-for="(m, i) in dlq.preview" :key="i" style="display:block">
            <span class="hc-name">{{ m.subject }}</span>
            <span class="hc-meta" style="display:block; max-width:none">{{ m.from }}</span>
          </li>
        </ul>
        <div style="margin-top: 10px; display:flex; gap:10px">
          <button @click="doReplayDlq">Rejouer</button>
          <button class="ghost" @click="doPurgeDlq">Vider</button>
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
