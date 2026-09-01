import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: () => import('./views/Overview.vue') },
    { path: '/ips', name: 'ips', component: () => import('./views/Ips.vue') },
    { path: '/sites', name: 'sites', component: () => import('./views/Sites.vue') },
    { path: '/stats', name: 'stats', component: () => import('./views/Stats.vue') },
  ],
})
