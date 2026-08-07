<script setup>
</script>

<template>
  <el-container>
    <el-header>
      <el-menu mode="horizontal" router :default-active="$route.path">
        <el-menu-item index="/">
          <el-icon><Document /></el-icon>
          翻译
        </el-menu-item>
        <el-menu-item index="/history">
          <el-icon><Clock /></el-icon>
          历史记录
        </el-menu-item>
        <el-menu-item index="/glossary">
          <el-icon><Notebook /></el-icon>
          术语表
        </el-menu-item>
        <!-- <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          设置
        </el-menu-item> -->
      </el-menu>
    </el-header>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<script>
import { Document, Clock, Notebook, Setting } from '@element-plus/icons-vue'
export default { components: { Document, Clock, Notebook, Setting } }
</script>

<style>
body { margin: 0; min-width: 320px; background: #f5f7fa; color: #202938; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.el-header { height: 58px; padding: 0; background: #fff; border-bottom: 1px solid #e4e9f0; }
.el-header .el-menu { border-bottom: 0; padding: 0 24px; }
.el-main { width: min(1180px, calc(100% - 48px)); max-width: 1180px; box-sizing: border-box; margin: 0 auto; padding: 32px 0 48px; }
@media (max-width: 640px) {
  .el-header .el-menu { padding: 0 8px; }
  .el-main { width: calc(100% - 28px); padding: 20px 0 32px; }
}
</style>
