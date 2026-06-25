<script setup>
import { ref, onMounted } from 'vue'
import { listUsers, createUser, deleteUser, changePassword, setUserActive, setAuditedEmails, setUserEmail, transferPerimeter, listTransferJobs, setRestoreImap } from '../api.js'

const emit = defineEmits(['expired'])

const users = ref([])
const error = ref('')
const form = ref({ username: '', password: '', role: 'user', email: '', display_name: '', audited_emails: '' })
const editingId = ref(null)
const newPassword = ref('')
const editingScopeId = ref(null)
const scopeInput = ref('')
const editingEmailId = ref(null)
const emailInput = ref('')
const jobs = ref([])
const imapEditUser = ref(null)
const imapForm = ref({ host: '', port: 993, username: '', password: '', ssl: true, folder: 'INBOX' })

function openImapEditor(u) {
  const r = u.restore_imap || {}
  imapForm.value = {
    host: r.host || '',
    port: r.port || 993,
    username: r.username || '',
    password: '',
    ssl: r.ssl !== false,
    folder: r.folder || 'INBOX',
  }
  imapEditUser.value = u
  error.value = ''
}
async function saveImapConfig() {
  await setRestoreImap(imapEditUser.value.id, { ...imapForm.value, port: Number(imapForm.value.port) })
}
async function saveImap() {
  try {
    await saveImapConfig()
    imapEditUser.value = null
    await refresh()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}
async function restoreViaImap() {
  if (!imapForm.value.host) {
    error.value = 'Renseignez le serveur IMAP.'
    return
  }
  try {
    await saveImapConfig() // s'assurer que la destination est enregistrée
    const u = imapEditUser.value
    imapEditUser.value = null
    await refresh()
    await launchRestore(u, 'imap', `la boîte IMAP ${imapForm.value.host}/${imapForm.value.folder} (APPEND)`)
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}
async function clearImap() {
  try {
    await setRestoreImap(imapEditUser.value.id, { host: '' }) // host vide = efface
    imapEditUser.value = null
    await refresh()
  } catch (e) {
    error.value = e.message
  }
}

async function refresh() {
  error.value = ''
  try {
    users.value = await listUsers()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

async function submit() {
  error.value = ''
  try {
    await createUser({
      username: form.value.username,
      password: form.value.password,
      role: form.value.role,
      email: form.value.email || null,
      display_name: form.value.display_name || null,
      // adresses auditées : séparées par virgule, point-virgule, espace ou retour ligne
      audited_emails:
        form.value.role === 'auditor'
          ? form.value.audited_emails.split(/[\s,;]+/).filter(Boolean)
          : null,
    })
    form.value = { username: '', password: '', role: 'user', email: '', display_name: '', audited_emails: '' }
    await refresh()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

function startEditPassword(u) {
  editingId.value = u.id
  editingScopeId.value = null
  editingEmailId.value = null
  newPassword.value = ''
  error.value = ''
}
function cancelEditPassword() {
  editingId.value = null
  newPassword.value = ''
}
async function savePassword(u) {
  if (!newPassword.value) {
    error.value = 'Mot de passe requis'
    return
  }
  try {
    await changePassword(u.id, newPassword.value)
    cancelEditPassword()
    alert(`Mot de passe modifié pour « ${u.username} »`)
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

function startEditEmail(u) {
  editingEmailId.value = u.id
  editingId.value = null
  editingScopeId.value = null
  emailInput.value = u.email || ''
  error.value = ''
}
function cancelEditEmail() {
  editingEmailId.value = null
  emailInput.value = ''
}
async function saveEmail(u) {
  try {
    await setUserEmail(u.id, emailInput.value.trim() || null)
    cancelEditEmail()
    await refresh()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

function startEditScope(u) {
  editingScopeId.value = u.id
  editingId.value = null
  editingEmailId.value = null
  scopeInput.value = (u.audited_emails || []).join(', ')
  error.value = ''
}
function cancelEditScope() {
  editingScopeId.value = null
  scopeInput.value = ''
}
async function saveScope(u) {
  const emails = scopeInput.value.split(/[\s,;]+/).filter(Boolean)
  try {
    await setAuditedEmails(u.id, emails)
    cancelEditScope()
    await refresh()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

async function doTransferSmtp(u) {
  if (!u.email) {
    error.value = 'Ce compte n’a pas d’adresse e-mail.'
    return
  }
  if (!confirm(`Restorer tous les mails du périmètre vers « ${u.email} » via SMTP ?`)) return
  await launchRestore(u, 'smtp', `« ${u.email} » via SMTP`)
}
async function launchRestore(u, restoreMethod, label) {
  error.value = ''
  try {
    const r = await transferPerimeter(u.id, restoreMethod)
    alert(`Restoration lancée en arrière-plan : ${r.total} mail(s) vers ${r.recipient} (job #${r.job_id}).`)
    await refreshJobs()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

async function refreshJobs() {
  try {
    jobs.value = await listTransferJobs()
  } catch (_) {}
}

async function toggleActive(u) {
  error.value = ''
  try {
    await setUserActive(u.id, !u.is_active)
    await refresh()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

async function remove(u) {
  if (!confirm(`Supprimer le compte « ${u.username} » ?`)) return
  error.value = ''
  try {
    await deleteUser(u.id)
    await refresh()
  } catch (e) {
    if (e.message === 'unauthorized') emit('expired')
    else error.value = e.message
  }
}

function roleLabel(role) {
  return { admin: 'Administrateur', auditor: 'Auditeur', user: 'Utilisateur' }[role] || role
}

onMounted(() => {
  refresh()
  refreshJobs()
})
</script>

<template>
  <section class="card">
    <h2>Comptes locaux</h2>

    <!-- Création d'un compte -->
    <div class="filters">
      <div><label>Identifiant</label><input v-model="form.username" /></div>
      <div>
        <label>{{ form.role === 'auditor' ? 'E-mail de l\'auditeur (son adresse, requis)' : 'E-mail (mails accessibles à l\'utilisateur)' }}</label>
        <input v-model="form.email" type="email" placeholder="alice@corp.com" />
      </div>
      <div><label>Nom affiché (optionnel)</label><input v-model="form.display_name" /></div>
      <div><label>Mot de passe</label><input v-model="form.password" type="password" /></div>
      <div>
        <label>Rôle</label>
        <select v-model="form.role">
          <option value="user">Utilisateur</option>
          <option value="auditor">Auditeur</option>
          <option value="admin">Administrateur</option>
        </select>
      </div>
      <div v-if="form.role === 'auditor'" style="grid-column: 1 / -1">
        <label>Adresses auditées (l'auditeur peut lire les mails de ces adresses — séparées par virgule, espace ou retour ligne)</label>
        <textarea v-model="form.audited_emails" rows="2" placeholder="alice@corp.com, bob@corp.com"></textarea>
      </div>
    </div>
    <div style="margin-top: 14px"><button @click="submit">Créer le compte</button></div>
    <p v-if="error" class="err">{{ error }}</p>

    <!-- Liste -->
    <table v-if="users.length">
      <thead>
        <tr><th>Identifiant</th><th>E-mail</th><th>Nom</th><th>Rôle</th><th>Périmètre audité</th><th>Actif</th><th>Créé le</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="u in users" :key="u.id">
          <td>{{ u.username }}</td>

          <!-- E-mail : édition inline au clic (sauf admin) -->
          <td>
            <template v-if="editingEmailId === u.id">
              <input
                v-model="emailInput"
                type="email"
                class="inline-edit"
                placeholder="adresse e-mail"
                @keyup.enter="saveEmail(u)"
                @keyup.esc="cancelEditEmail"
              />
              <button class="link" @click="saveEmail(u)">✓</button>
              <button class="link" @click="cancelEditEmail">✕</button>
            </template>
            <span
              v-else-if="u.role !== 'admin'"
              class="editable"
              title="Cliquer pour modifier l'adresse e-mail"
              @click="startEditEmail(u)"
            >{{ u.email || '— (ajouter)' }}</span>
            <span v-else>{{ u.email || '—' }}</span>
          </td>

          <td>{{ u.display_name }}</td>
          <td><span class="pill">{{ roleLabel(u.role) }}</span></td>

          <!-- Périmètre audité : édition inline au clic (auditeurs) -->
          <td>
            <template v-if="editingScopeId === u.id">
              <input
                v-model="scopeInput"
                class="inline-edit scope-edit"
                placeholder="adresses séparées par virgule"
                @keyup.enter="saveScope(u)"
                @keyup.esc="cancelEditScope"
              />
              <button class="link" @click="saveScope(u)">✓</button>
              <button class="link" @click="cancelEditScope">✕</button>
            </template>
            <span
              v-else-if="u.role === 'auditor'"
              class="editable"
              title="Cliquer pour modifier le périmètre audité"
              @click="startEditScope(u)"
            >{{ (u.audited_emails || []).join(', ') || '— (définir)' }}</span>
            <span v-else>—</span>
          </td>

          <td>{{ u.is_active ? '✓' : '—' }}</td>
          <td>{{ (u.created_at || '').slice(0, 10) }}</td>
          <td>
            <template v-if="editingId === u.id">
              <input
                v-model="newPassword"
                type="password"
                placeholder="Nouveau mot de passe"
                class="inline-edit"
                @keyup.enter="savePassword(u)"
                @keyup.esc="cancelEditPassword"
              />
              <button class="link" @click="savePassword(u)">✓</button>
              <button class="link" @click="cancelEditPassword">✕</button>
            </template>
            <template v-else>
              <button class="link" @click="startEditPassword(u)">Mot de passe</button>
              <button v-if="u.role !== 'admin'" class="link sep" @click="openImapEditor(u)">
                {{ u.restore_imap ? 'Restoration IMAP ✓' : 'Restoration IMAP' }}
              </button>
              <button v-if="u.role !== 'admin'" class="link sep" @click="doTransferSmtp(u)">Restoration SMTP</button>
              <button v-if="!u.protected" class="link sep" @click="toggleActive(u)">
                {{ u.is_active ? 'Désactiver' : 'Activer' }}
              </button>
              <button v-if="!u.protected" class="link sep" @click="remove(u)">Supprimer</button>
              <span v-if="u.protected" class="muted sep" title="Compte administrateur principal">protégé</span>
            </template>
          </td>
        </tr>
      </tbody>
    </table>
  </section>

  <section class="card" style="margin-top: 14px">
    <h2 style="display:flex; align-items:center; gap:10px">
      Restaurations récentes
      <button class="link" @click="refreshJobs">↻ Rafraîchir</button>
    </h2>
    <table v-if="jobs.length">
      <thead>
        <tr><th>#</th><th>Auditeur</th><th>Destinataire</th><th>Progression</th><th>Statut</th><th>Terminé</th></tr>
      </thead>
      <tbody>
        <tr v-for="j in jobs" :key="j.id">
          <td>{{ j.id }}</td>
          <td>{{ j.auditor }}</td>
          <td>{{ j.recipient }}</td>
          <td>{{ j.sent }} / {{ j.total }}</td>
          <td>{{ j.status === 'running' ? 'en cours' : j.status === 'done' ? 'terminé' : 'erreur' }}{{ j.error ? ' : ' + j.error : '' }}</td>
          <td>{{ (j.finished_at || '').slice(0, 16).replace('T', ' ') || '—' }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else class="muted">Aucun transfert.</p>
  </section>

  <!-- Configuration de la destination IMAP de restauration -->
  <div v-if="imapEditUser" class="overlay" @click.self="imapEditUser = null">
    <div class="modal card">
      <h2>Destination IMAP de restauration — {{ imapEditUser.username }}</h2>
      <p class="muted">Si renseignée, « Restorer » dépose les mails du périmètre directement dans cette boîte (IMAP APPEND, façon imapsync). Sinon, repli sur le relais SMTP vers l'adresse du compte.</p>
      <div class="filters">
        <div><label>Serveur IMAP</label><input v-model="imapForm.host" placeholder="imap.example.com" /></div>
        <div><label>Port</label><input v-model="imapForm.port" type="number" /></div>
        <div><label>Identifiant</label><input v-model="imapForm.username" /></div>
        <div>
          <label>Mot de passe {{ (imapEditUser.restore_imap && imapEditUser.restore_imap.password_set) ? '(défini — vide = conserver)' : '' }}</label>
          <input v-model="imapForm.password" type="password" />
        </div>
        <div><label>Dossier</label><input v-model="imapForm.folder" placeholder="INBOX" /></div>
        <div>
          <label>Options</label>
          <label class="opt"><input type="checkbox" v-model="imapForm.ssl" /> SSL/TLS</label>
        </div>
      </div>
      <div style="margin-top: 14px; display:flex; gap:10px; flex-wrap:wrap">
        <button @click="restoreViaImap">Restorer maintenant (IMAP)</button>
        <button class="ghost" @click="saveImap">Enregistrer la destination</button>
        <button v-if="imapEditUser.restore_imap" class="ghost" @click="clearImap">Supprimer</button>
        <button class="ghost" @click="imapEditUser = null">Annuler</button>
      </div>
      <p v-if="error" class="err">{{ error }}</p>
    </div>
  </div>
</template>

<style scoped>
.inline-edit {
  width: auto;
  display: inline-block;
  padding: 3px 6px;
  margin-right: 6px;
}
.scope-edit { width: 280px; }
.sep { margin-left: 12px; }
.editable {
  cursor: pointer;
  border-bottom: 1px dashed var(--muted);
}
.editable:hover { color: var(--accent); border-bottom-color: var(--accent); }
.overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.6);
  display: flex; align-items: flex-start; justify-content: center; padding: 50px 16px; overflow: auto;
}
.modal { max-width: 560px; width: 100%; }
.opt { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--text); }
.opt input { width: auto; }
textarea {
  width: 100%;
  padding: 5px 8px;
  border-radius: 3px;
  border: 1px solid var(--border);
  background: var(--input-bg);
  color: var(--text);
  font-size: 13px;
  font-family: inherit;
  resize: vertical;
}
</style>
