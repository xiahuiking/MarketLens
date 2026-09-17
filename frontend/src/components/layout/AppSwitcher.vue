<template>
  <div class="app-switcher">
    <div
      v-for="app in apps"
      :key="app.name"
      class="switcher-tab"
      :class="{ active: appsStore.activeApp === app.name, locked: app.locked }"
      @click="handleTabClick(app)"
    >
      <span class="status-dot" :class="appsStore.apps[app.name]?.status || 'stopped'" />
      <span class="tab-label">{{ app.label }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useAppsStore } from '@/stores/apps'
import { useReportStore } from '@/stores/report'

const appsStore = useAppsStore()
const reportStore = useReportStore()

const apps = computed(() => [
  { name: 'insight', label: 'Insight', locked: false },
  { name: 'media', label: 'Media', locked: false },
  { name: 'query', label: 'Query', locked: false },
  { name: 'forum', label: 'Forum', locked: false },
  {
    name: 'report',
    label: 'Report',
    locked: !reportStore.enginesReady,
  },
])

function handleTabClick(app: { name: string; locked: boolean }) {
  if (app.locked) {
    reportStore.fetchStatus()
    return
  }
  appsStore.setActiveApp(app.name)
}
</script>

<style scoped>
.app-switcher {
  display: flex;
  gap: 0;
  background: #1a1a2e;
  padding: 4px;
  border-bottom: 2px solid #1a1a2e;
}
.switcher-tab {
  flex: 1;
  text-align: center;
  padding: 10px 0;
  color: #888;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  border-radius: 0;
  border: 2px solid transparent;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.switcher-tab:hover {
  color: #fff;
  background: rgba(255,255,255,0.08);
  border-bottom-color: #e6a23c;
}
.switcher-tab.active {
  color: #fff;
  background: rgba(255,255,255,0.15);
  border-bottom-color: #67c23a;
}
.switcher-tab.locked {
  opacity: 0.4;
  cursor: not-allowed;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.status-dot.running { background: #67c23a; }
.status-dot.stopped { background: #f56c6c; }
.status-dot.starting { background: #e6a23c; }
.status-dot.error { background: #f56c6c; }
</style>
