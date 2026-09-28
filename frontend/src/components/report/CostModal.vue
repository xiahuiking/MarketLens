<template>
  <el-dialog
    v-model="visible"
    class="cost-modal"
    width="820px"
    top="7vh"
    append-to-body
    destroy-on-close
    title="成本预览"
  >
    <CostPanel ref="panelRef" />
    <template #footer>
      <div class="cost-modal-footer">
        <span class="cost-modal-hint">打开期间每 5 秒自动刷新一次</span>
        <el-button size="small" :icon="Refresh" @click="refresh">立即刷新</el-button>
        <el-button size="small" type="primary" @click="close">关闭</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import CostPanel from './CostPanel.vue'

const visible = defineModel<boolean>({ required: true })

const panelRef = ref<InstanceType<typeof CostPanel> | null>(null)

function close() {
  visible.value = false
}

function refresh() {
  panelRef.value?.refresh()
}

// destroy-on-close 会在每次打开时重新挂载 CostPanel，挂载即自动拉取数据；
// 若弹窗被复用来重新打开，也主动补一次刷新。
watch(visible, (open) => {
  if (open) panelRef.value?.refresh()
})
</script>

<style scoped>
.cost-modal-footer {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cost-modal-hint {
  flex: 1;
  text-align: left;
  color: #909399;
  font-size: 12px;
}
</style>

<style>
/* el-dialog 通过 append-to-body 渲染到 body，需用非 scoped 样式约束高度与滚动 */
.cost-modal .el-dialog__body {
  max-height: 68vh;
  overflow-y: auto;
  padding-top: 8px;
}
.cost-modal .cost-panel {
  margin-top: 0;
}
</style>
