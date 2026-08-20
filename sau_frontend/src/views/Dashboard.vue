<template>
  <section class="dashboard">
    <header>
      <h1>Social Auto Upload</h1>
      <p>离线桌面控制台与命令行共享同一套受治理发布核心。</p>
    </header>

    <div class="action-grid">
      <el-card
        v-for="action in actions"
        :key="action.path"
        class="action-card"
        shadow="hover"
        tabindex="0"
        @click="router.push(action.path)"
        @keyup.enter="router.push(action.path)"
      >
        <h2>{{ action.title }}</h2>
        <p>{{ action.description }}</p>
        <el-button type="primary" text>打开</el-button>
      </el-card>
    </div>

    <el-card class="material-summary" shadow="never">
      <div>
        <h2>本机素材</h2>
        <p>当前素材数量：{{ materialCount }}</p>
      </div>
      <el-button :loading="loading" @click="loadMaterialCount">刷新</el-button>
    </el-card>
  </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { materialApi } from '@/api/material'

const router = useRouter()
const loading = ref(false)
const materialCount = ref(0)
const actions = [
  { path: '/account-management', title: '账号登录', description: '在可见浏览器中完成平台登录。' },
  { path: '/material-management', title: '素材管理', description: '上传、预览、下载和删除本机素材。' },
  { path: '/publish-center', title: '发布中心', description: '提交任务、查看七种状态并执行人工确认。' }
]

const loadMaterialCount = async () => {
  loading.value = true
  try {
    const response = await materialApi.getAllMaterials()
    materialCount.value = response.data.materials.length
  } finally {
    loading.value = false
  }
}

onMounted(loadMaterialCount)
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

.dashboard {
  max-width: 1040px;
  margin: 0 auto;
}

header {
  margin-bottom: 24px;

  h1 {
    margin: 0 0 8px;
    color: $text-primary;
  }

  p {
    margin: 0;
    color: $text-secondary;
  }
}

.action-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
}

.action-card {
  cursor: pointer;

  h2 {
    margin-top: 0;
  }
}

.material-summary {
  margin-top: 18px;

  :deep(.el-card__body) {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  h2,
  p {
    margin: 0;
  }

  p {
    margin-top: 6px;
    color: $text-secondary;
  }
}

@media (max-width: 760px) {
  .action-grid {
    grid-template-columns: 1fr;
  }
}
</style>
