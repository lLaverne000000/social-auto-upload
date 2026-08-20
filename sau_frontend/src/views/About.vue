<template>
  <div class="about">
    <el-card class="about-card">
      <div class="about-header">
        <h1>自媒体自动化运营系统</h1>
        <p class="version">social-auto-upload</p>
      </div>

      <el-divider />

      <div class="about-section">
        <h3>系统简介</h3>
        <p>
          Social Auto Upload 提供 GUI 与 CLI 两种入口，两者共享同一套受治理的发布核心。
          桌面界面用于账号登录、本机素材库管理、单个视频任务提交和状态跟踪。
        </p>
      </div>

      <div class="about-section">
        <h3>支持平台</h3>
        <div class="platform-tags">
          <el-tag type="danger">抖音</el-tag>
          <el-tag type="success">快手</el-tag>
          <el-tag type="warning">视频号</el-tag>
          <el-tag type="info">小红书</el-tag>
        </div>
      </div>

      <div class="about-section">
        <h3>核心功能</h3>
        <ul class="feature-list">
          <li>GUI 与 CLI 共享发布、登录和风险控制逻辑</li>
          <li>本机素材库支持上传、预览、下载和删除</li>
          <li>发布任务显示排队、运行、确认、成功、失败和阻止等状态</li>
          <li>默认使用可见浏览器，并在最终发布前要求人工确认</li>
          <li>安装包内置运行时与浏览器，可离线运行；访问发布平台仍需要网络</li>
        </ul>
      </div>

      <div class="about-section">
        <h3>技术栈</h3>
        <div class="tech-tags">
          <el-tag effect="plain">Vue 3</el-tag>
          <el-tag effect="plain">Element Plus</el-tag>
          <el-tag effect="plain">Pinia</el-tag>
          <el-tag effect="plain">Flask</el-tag>
          <el-tag effect="plain">Playwright</el-tag>
          <el-tag effect="plain">SQLite</el-tag>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
// 关于页面组件
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

.about {
  max-width: 700px;
  margin: 0 auto;

  .about-card {
    .about-header {
      text-align: center;

      h1 {
        color: $text-primary;
        margin: 0 0 8px 0;
        font-size: 24px;
      }

      .version {
        color: $text-secondary;
        font-size: 14px;
        margin: 0;
      }
    }

    .about-section {
      margin-bottom: 24px;

      h3 {
        font-size: 16px;
        color: $text-primary;
        margin: 0 0 12px 0;
      }

      p {
        color: $text-secondary;
        line-height: 1.8;
        margin: 0;
      }

      .platform-tags,
      .tech-tags {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
      }

      .feature-list {
        margin: 0;
        padding-left: 20px;
        color: $text-secondary;
        line-height: 2;
      }
    }
  }
}
</style>
