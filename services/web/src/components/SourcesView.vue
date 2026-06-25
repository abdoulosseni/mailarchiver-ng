<script setup>
import { ref, onMounted } from 'vue'
import { listFetchSources, createFetchSource, deleteFetchSource, runFetchSource } from '../api.js'

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
    alert(`Source « ${s.name} » : ${r.status}`)
    await refresh()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

async function remove(s) {
  if (!confirm(`Supprimer la source « ${s.name} » ?`)) return
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
    <h2>Collecte via SMTP (journaling)</h2>
    <p class="muted">
      Contrairement à IMAP/POP3 (le système <em>relève</em> les boîtes), le SMTP est <strong>poussé</strong> :
      votre serveur de messagerie (MTA) envoie une <strong>copie de chaque mail</strong> à la passerelle SMTP
      intégrée de MailArchiver-NG. C'est la méthode recommandée pour un archivage exhaustif en temps réel.
    </p>
    <p class="muted">
      Passerelle SMTP : <code>{{ smtpHost }}:{{ smtpPort }}</code> — STARTTLS pris en charge, aucune authentification requise
      (à protéger par filtrage réseau / pare-feu).
    </p>

    <h3>Exemple de configuration Postfix</h3>
    <p class="muted">1. Envoyer une copie de chaque message à l'archiveur (<code>/etc/postfix/main.cf</code>) :</p>
    <pre class="cfg"># Copie cachée systématique de tous les mails vers l'archiveur
always_bcc = archive@mailarchiver.invalid

# Router ce domaine vers la passerelle SMTP de MailArchiver-NG
transport_maps = hash:/etc/postfix/transport

# STARTTLS opportuniste vers l'archiveur
smtp_tls_security_level = may</pre>

    <p class="muted">2. Définir le transport (<code>/etc/postfix/transport</code>) :</p>
    <pre class="cfg">mailarchiver.invalid    smtp:[{{ smtpHost }}]:{{ smtpPort }}</pre>

    <p class="muted">3. Appliquer :</p>
    <pre class="cfg">postmap /etc/postfix/transport
systemctl reload postfix</pre>

    <p class="muted">
      Variante par utilisateur/domaine : utilisez <code>sender_bcc_maps</code> / <code>recipient_bcc_maps</code>
      au lieu de <code>always_bcc</code> pour n'archiver qu'un périmètre. Les crochets <code>[ ]</code> autour de
      l'hôte désactivent la résolution MX (livraison directe).
    </p>
  </section>

  <section class="card">
    <h2>Sources de collecte IMAP / POP3</h2>
    <p class="muted">Le système relève ces boîtes périodiquement et archive les mails (en plus du SMTP).</p>

    <div class="filters">
      <div><label>Nom</label><input v-model="form.name" placeholder="Boîte support" /></div>
      <div>
        <label>Protocole</label>
        <select v-model="form.protocol" @change="onProtoChange">
          <option value="imap">IMAP</option>
          <option value="pop3">POP3</option>
        </select>
      </div>
      <div><label>Serveur</label><input v-model="form.host" placeholder="mail.example.com" /></div>
      <div><label>Port</label><input v-model="form.port" type="number" /></div>
      <div><label>Identifiant</label><input v-model="form.username" /></div>
      <div><label>Mot de passe</label><input v-model="form.password" type="password" /></div>
      <div v-if="form.protocol === 'imap'"><label>Dossier</label><input v-model="form.folder" /></div>
      <div><label>Intervalle (min)</label><input v-model="form.interval_minutes" type="number" /></div>
      <div>
        <label>Options</label>
        <label class="opt"><input type="checkbox" v-model="form.use_ssl" @change="onProtoChange" /> SSL/TLS</label>
        <label class="opt"><input type="checkbox" v-model="form.delete_after" /> Supprimer après collecte</label>
      </div>
    </div>
    <div style="margin-top: 14px"><button @click="submit">Ajouter la source</button></div>
    <p v-if="error" class="err">{{ error }}</p>

    <table v-if="sources.length">
      <thead>
        <tr><th>Nom</th><th>Protocole</th><th>Serveur</th><th>Identifiant</th><th>Intervalle</th><th>Dernière collecte</th><th>Statut</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="s in sources" :key="s.id">
          <td>{{ s.name }}</td>
          <td>{{ s.protocol.toUpperCase() }}{{ s.use_ssl ? 'S' : '' }}</td>
          <td>{{ s.host }}:{{ s.port }}</td>
          <td>{{ s.username }}</td>
          <td>{{ s.interval_minutes }} min</td>
          <td>{{ (s.last_run || '').slice(0, 16).replace('T', ' ') || '—' }}</td>
          <td>{{ s.last_status || '—' }}</td>
          <td>
            <button class="link" @click="runNow(s)">Relever maintenant</button>
            <button class="link sep" @click="remove(s)">Supprimer</button>
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
