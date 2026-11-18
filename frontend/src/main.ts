import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { initAuthToken } from './api/client'
import './assets/main.css'
import './assets/theme.css'
import './assets/messages.css'
import './assets/session-status.css'
import './assets/layout.css'

initAuthToken()

createApp(App).use(createPinia()).mount('#app')
