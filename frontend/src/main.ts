import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { initAuthToken } from './api/client'
import './assets/main.css'

initAuthToken()

createApp(App).use(createPinia()).mount('#app')
