import { createApp } from 'vue'
import App from './App.vue'
import { initI18n } from './i18n.js'
import './style.css'

// Applique le thème enregistré avant le rendu (évite tout flash de couleur).
// Mode clair par défaut (si aucune préférence enregistrée).
const savedTheme = localStorage.getItem('ma_theme') || 'light'
document.documentElement.setAttribute('data-theme', savedTheme)

// Applique la langue enregistrée (attribut <html lang>).
initI18n()

createApp(App).mount('#app')
