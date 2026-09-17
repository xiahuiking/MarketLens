<template>
  <div class="the-header">
    <div class="header-left">
      <el-image
        :src="logoUrl"
        alt="Logo"
        class="logo"
        fit="contain"
      />
      <h1 class="title">🐛舆分析平台 - Chill SentinelAI</h1>
    </div>
    <div class="header-right">
      <el-button
        :icon="Refresh"
        circle
        @click="$emit('refresh')"
        title="刷新状态"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { Refresh } from '@element-plus/icons-vue'
import logoUrl from '@/assets/logo.png'

defineEmits<{
  refresh: []
}>()
</script>

<style scoped>
.the-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 20px;
  background: #1a1a2e;
  color: #fff;
  border-bottom: 3px solid #e6a23c;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.logo {
  width: 40px;
  height: 40px;
}
.title {
  font-size: 18px;
  font-weight: 800;
  letter-spacing: 1px;
  margin: 0;
}
.header-right {
  display: flex;
  gap: 8px;
}
</style>
