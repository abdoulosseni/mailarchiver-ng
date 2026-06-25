import { createApp } from 'vue'
import App from './App.vue'
import './style.css'

// Applique le thème enregistré avant le rendu (évite tout flash de couleur).
// Mode clair par défaut (si aucune préférence enregistrée).
const savedTheme = localStorage.getItem('ma_theme') || 'light'
document.documentElement.setAttribute('data-theme', savedTheme)

createApp(App).mount('#app')
