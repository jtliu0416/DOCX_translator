import { createRouter, createWebHistory } from 'vue-router'
import Translate from '../views/Translate.vue'
import History from '../views/History.vue'
import Glossary from '../views/Glossary.vue'
import Settings from '../views/Settings.vue'

const routes = [
  { path: '/', name: 'translate', component: Translate },
  { path: '/history', name: 'history', component: History },
  { path: '/glossary', name: 'glossary', component: Glossary },
  { path: '/settings', name: 'settings', component: Settings },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
