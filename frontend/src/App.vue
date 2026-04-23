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
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          设置
        </el-menu-item>
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
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.el-header { padding: 0; }
.el-main { max-width: 900px; margin: 0 auto; }
</style>
