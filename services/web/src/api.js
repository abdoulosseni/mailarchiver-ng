// Client API. Les URL sont relatives : Nginx (prod) ou le proxy Vite (dev)
// redirigent vers le service `api`, donc aucune configuration CORS côté client.

const TOKEN_KEY = 'ma_token'
const USER_KEY = 'ma_user'
const ADMIN_KEY = 'ma_admin'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}
export function getUser() {
  return localStorage.getItem(USER_KEY)
}
export function isAdmin() {
  return localStorage.getItem(ADMIN_KEY) === '1'
}
export function setSession(token, user, admin) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, user)
  localStorage.setItem(ADMIN_KEY, admin ? '1' : '0')
}
export function clearSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  localStorage.removeItem(ADMIN_KEY)
}

async function authFetch(url, opts = {}) {
  const headers = { ...(opts.headers || {}), Authorization: 'Bearer ' + getToken() }
  const res = await fetch(url, { ...opts, headers })
  if (res.status === 401) {
    clearSession()
    throw new Error('unauthorized')
  }
  return res
}

export async function login(username, password) {
  const res = await fetch('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Échec de connexion')
  }
  return res.json()
}

export async function searchAdvanced(filters, searchAfter = null, size = 50) {
  const body = { ...filters }
  if (searchAfter) body.search_after = searchAfter
  const res = await authFetch(`/search/advanced?size=${size}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Erreur lors de la recherche')
  }
  const data = await res.json()
  return {
    total: data.total || 0,
    totalEstimated: !!data.total_estimated,
    results: data.results || [],
    nextSearchAfter: data.next_search_after || null,
  }
}

export async function getMessage(id) {
  const res = await authFetch('/messages/' + id)
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Erreur lors de la consultation')
  }
  return res.json()
}

export async function fetchEml(id) {
  const res = await authFetch('/messages/' + id + '/eml')
  if (!res.ok) throw new Error('Erreur lors de l’export')
  const integrity = res.headers.get('X-Archive-Integrity')
  const blob = await res.blob()
  return { blob, integrity }
}

// ── Gestion des comptes (admin) ───────────────────────────────────

export async function listUsers() {
  const res = await authFetch('/users')
  return (await res.json()).users || []
}

export async function createUser(payload) {
  const res = await authFetch('/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Erreur lors de la création')
  }
  return res.json()
}

// ── Paramètres globaux (admin) ────────────────────────────────────

export async function getDlq() {
  const res = await authFetch('/dlq')
  return res.json()
}
export async function replayDlq() {
  const res = await authFetch('/dlq/replay', { method: 'POST' })
  if (!res.ok) throw new Error('Erreur lors du rejeu')
  return res.json()
}
export async function purgeDlq() {
  const res = await authFetch('/dlq/purge', { method: 'POST' })
  if (!res.ok) throw new Error('Erreur lors de la purge')
  return res.json()
}

export async function getThroughput() {
  const res = await authFetch('/metrics/throughput')
  return res.json()
}

export async function getHealth() {
  const res = await authFetch('/health/components')
  return res.json()
}

export async function getStats() {
  const res = await authFetch('/stats')
  return res.json()
}

export async function getAppSettings() {
  const res = await authFetch('/settings')
  return res.json()
}

export async function updateAppSettings(payload) {
  const res = await authFetch('/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Erreur lors de l’enregistrement')
  }
  return res.json()
}

// ── Sources de collecte IMAP/POP3 (admin) ─────────────────────────

export async function listFetchSources() {
  const res = await authFetch('/fetch-sources')
  return (await res.json()).sources || []
}

export async function createFetchSource(payload) {
  const res = await authFetch('/fetch-sources', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Erreur lors de la création')
  }
  return res.json()
}

export async function deleteFetchSource(id) {
  const res = await authFetch('/fetch-sources/' + id, { method: 'DELETE' })
  if (!res.ok) throw new Error('Erreur lors de la suppression')
  return res.json()
}

export async function runFetchSource(id) {
  const res = await authFetch('/fetch-sources/' + id + '/run', { method: 'POST' })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Échec de la collecte')
  }
  return res.json()
}

export async function changeOwnPassword(oldPassword, newPassword) {
  const res = await authFetch('/auth/change-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Erreur lors du changement de mot de passe')
  }
  return res.json()
}

export async function changePassword(id, password) {
  const res = await authFetch('/users/' + id + '/password', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Erreur lors du changement de mot de passe')
  }
  return res.json()
}

export async function transferPerimeter(id, restoreMethod = 'auto') {
  const res = await authFetch('/users/' + id + '/transfer-perimeter?method=' + restoreMethod, { method: 'POST' })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Échec du transfert')
  }
  return res.json()
}

export async function listTransferJobs() {
  const res = await authFetch('/transfer-jobs')
  return (await res.json()).jobs || []
}

export async function setUserEmail(id, email) {
  const res = await authFetch('/users/' + id + '/email', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Erreur lors de la modification de l’e-mail')
  }
  return res.json()
}

export async function setRestoreImap(id, payload) {
  const res = await authFetch('/users/' + id + '/restore-imap', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Erreur lors de la configuration IMAP')
  }
  return res.json()
}

export async function setAuditedEmails(id, emails) {
  const res = await authFetch('/users/' + id + '/audited-emails', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ audited_emails: emails }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Erreur lors de la modification du périmètre')
  }
  return res.json()
}

export async function setUserActive(id, isActive) {
  const res = await authFetch('/users/' + id + '/active', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: isActive }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Erreur lors du changement de statut')
  }
  return res.json()
}

export async function deleteUser(id) {
  const res = await authFetch('/users/' + id, { method: 'DELETE' })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Erreur lors de la suppression')
  }
  return res.json()
}

export async function setLegalHold(id, hold) {
  const res = await authFetch('/messages/' + id + '/legal-hold', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hold }),
  })
  if (!res.ok) throw new Error('Erreur conservation légale')
  return res.json()
}

export async function forwardMessage(id, recipients) {
  const res = await authFetch('/messages/forward', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_id: id, recipients }),
  })
  if (!res.ok) throw new Error('Erreur lors du transfert')
  return res.json()
}
