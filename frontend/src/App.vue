<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from './stores/app'
import { useDataStore } from './stores/data'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const dataStore = useDataStore()

const active = computed(() => route.path)

const titles: Record<string, string> = {
  '/': '总览仪表盘',
  '/ips': 'IP 列表',
  '/sites': '站点视图',
  '/stats': '统计分析',
}
const title = computed(() => titles[route.path] || 'NetFountain')

function go(path: string) {
  router.push(path)
}

onMounted(() => {
  appStore.apply()
  dataStore.start()
})
onBeforeUnmount(() => dataStore.stop())
</script>

<template>
  <el-container class="layout">
    <el-aside width="200px" class="aside">
      <div class="brand">
        <img class="brand-logo" src="/logo.png" alt="NetFountain" />
        <span>NetFountain</span>
      </div>
      <el-menu :default-active="active" @select="go">
        <el-menu-item index="/">总览</el-menu-item>
        <el-menu-item index="/ips">IP 列表</el-menu-item>
        <el-menu-item index="/sites">站点视图</el-menu-item>
        <el-menu-item index="/stats">统计分析</el-menu-item>
      </el-menu>
      <div class="aside-footer">
        <a
          href="https://github.com/Stars-22/NetFountain"
          target="_blank"
          rel="noopener noreferrer"
        >NetFountain · GitHub</a>
      </div>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="title">{{ title }}</span>
        <div class="right">
          <span v-if="dataStore.lastUpdated" class="updated">
            更新于 {{ new Date(dataStore.lastUpdated).toLocaleTimeString() }}
          </span>
          <el-switch
            :model-value="appStore.dark"
            @change="appStore.setDark"
            inline-prompt
            active-text="暗"
            inactive-text="亮"
          />
        </div>
      </el-header>
      <el-main class="main">
        <el-alert
          v-if="dataStore.error"
          :title="'接口异常：' + dataStore.error"
          type="error"
          show-icon
          :closable="false"
          class="error-bar"
        />
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100%;
}
.aside {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-right: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
}
.aside :deep(.el-menu) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}
.aside-footer {
  flex-shrink: 0;
  padding: 12px;
  border-top: 1px solid var(--el-border-color-light);
  text-align: center;
}
.aside-footer a {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  text-decoration: none;
}
.aside-footer a:hover {
  color: var(--el-color-primary);
}
.brand {
  flex-shrink: 0;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-weight: 700;
  font-size: 18px;
  color: var(--el-color-primary);
}
.brand-logo {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  object-fit: contain;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
}
.title {
  font-size: 16px;
  font-weight: 600;
}
.right {
  display: flex;
  align-items: center;
  gap: 16px;
}
.updated {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.main {
  background: var(--el-bg-color-page);
}
.error-bar {
  margin-bottom: 12px;
}
</style>
