<script setup>
import { ref } from 'vue'
import { getToken, getUser, isAdmin, clearSession } from './api.js'
import { t, locale, setLocale, SUPPORTED } from './i18n.js'
import LoginView from './components/LoginView.vue'
import SearchView from './components/SearchView.vue'
import LiveView from './components/LiveView.vue'
import UsersView from './components/UsersView.vue'
import SourcesView from './components/SourcesView.vue'
import SettingsView from './components/SettingsView.vue'
import AccountView from './components/AccountView.vue'

const authed = ref(!!getToken())
const user = ref(getUser())
const admin = ref(isAdmin())
const tab = ref('search')
const theme = ref(document.documentElement.getAttribute('data-theme') || 'dark')

function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  document.documentElement.setAttribute('data-theme', theme.value)
  localStorage.setItem('ma_theme', theme.value)
}

function onLogin() {
  authed.value = true
  user.value = getUser()
  admin.value = isAdmin()
  tab.value = 'search'
}
function logout() {
  clearSession()
  authed.value = false
}
</script>

<template>
  <header>
    <h1>📨 MailArchiver-NG</h1>
    <div class="header-right">
      <div v-if="authed" class="userbar">
        <nav class="tabs">
          <button class="tab" :class="{ active: tab === 'search' }" @click="tab = 'search'">{{ t('app.tabSearch') }}</button>
          <button class="tab" :class="{ active: tab === 'live' }" @click="tab = 'live'">{{ t('app.tabLive') }}</button>
          <button v-if="admin" class="tab" :class="{ active: tab === 'users' }" @click="tab = 'users'">{{ t('app.tabUsers') }}</button>
          <button v-if="admin" class="tab" :class="{ active: tab === 'sources' }" @click="tab = 'sources'">{{ t('app.tabSources') }}</button>
          <button v-if="admin" class="tab" :class="{ active: tab === 'settings' }" @click="tab = 'settings'">{{ t('app.tabSettings') }}</button>
          <button class="tab" :class="{ active: tab === 'account' }" @click="tab = 'account'">{{ t('app.tabAccount') }}</button>
        </nav>
        <span>{{ user }}</span>
        <button class="ghost" @click="logout">{{ t('app.logout') }}</button>
      </div>
      <div class="lang" :title="t('app.language')">
        <button
          v-for="l in SUPPORTED"
          :key="l"
          class="tab"
          :class="{ active: locale === l }"
          @click="setLocale(l)"
        >{{ l.toUpperCase() }}</button>
      </div>
      <button class="ghost" :title="theme === 'dark' ? t('app.toLight') : t('app.toDark')" @click="toggleTheme">
        {{ theme === 'dark' ? t('app.light') : t('app.dark') }}
      </button>
    </div>
  </header>
  <main>
    <LoginView v-if="!authed" @logged-in="onLogin" />
    <template v-else>
      <SearchView v-if="tab === 'search'" @expired="logout" />
      <LiveView v-else-if="tab === 'live'" @expired="logout" />
      <UsersView v-else-if="tab === 'users' && admin" @expired="logout" />
      <SourcesView v-else-if="tab === 'sources' && admin" @expired="logout" />
      <SettingsView v-else-if="tab === 'settings' && admin" @expired="logout" />
      <AccountView v-else-if="tab === 'account'" @expired="logout" />
    </template>
  </main>
  <footer class="app-footer">
    {{ t('app.footer') }}
  </footer>
</template>

<style>
.tabs { display: flex; gap: 6px; margin-right: 8px; }
.tab { background: transparent; color: var(--muted); border: 0; padding: 6px 10px; border-radius: 6px; }
.tab.active { background: var(--border); color: var(--text); }
.lang { display: flex; gap: 2px; margin-right: 8px; }
.lang .tab { padding: 6px 8px; font-size: 12px; font-weight: 600; }
</style>
