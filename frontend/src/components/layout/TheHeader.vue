<template>
  <div class="the-header">
    <div class="header-left">
      <el-image
        :src="logoUrl"
        alt="Logo"
        class="logo"
        fit="contain"
      />
      <h1 class="title">MarketLens · 电商商品评论竞品分析平台</h1>
    </div>
    <div class="header-right">
      <el-button
        class="cost-preview-btn"
        size="small"
        :icon="Wallet"
        @click="costModalVisible = true"
        title="查看本次分析的 token 与花费"
      >
        成本预览
      </el-button>
      <el-button
        :icon="Refresh"
        circle
        @click="$emit('refresh')"
        title="刷新状态"
      />
    </div>
    <CostModal v-model="costModalVisible" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { Refresh, Wallet } from '@element-plus/icons-vue'
import logoUrl from '@/assets/logo.png'
import CostModal from '@/components/report/CostModal.vue'

const costModalVisible = ref(false)

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
  align-items: center;
  gap: 8px;
}
.cost-preview-btn {
  background: transparent;
  border-color: #e6a23c;
  color: #e6a23c;
}
.cost-preview-btn:hover,
.cost-preview-btn:focus {
  background: #e6a23c;
  border-color: #e6a23c;
  color: #1a1a2e;
}
</style>
