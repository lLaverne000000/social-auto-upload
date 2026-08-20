<template>
  <section class="material-management">
    <header class="page-header">
      <div>
        <h1>素材管理</h1>
        <p>素材上传后由本机服务保存，页面只使用不透明素材 ID。</p>
      </div>
      <div class="header-actions">
        <el-button type="primary" @click="uploadDialogVisible = true">上传素材</el-button>
        <el-button :loading="loading" @click="fetchMaterials">刷新</el-button>
      </div>
    </header>

    <el-card shadow="never">
      <el-input
        v-model="searchKeyword"
        class="search"
        placeholder="按文件名搜索"
        clearable
      />

      <el-table v-if="filteredMaterials.length" :data="filteredMaterials">
        <el-table-column prop="name" label="文件名" min-width="260" />
        <el-table-column label="大小" width="130">
          <template #default="scope">{{ formatBytes(scope.row.sizeBytes) }}</template>
        </el-table-column>
        <el-table-column label="上传时间" width="190">
          <template #default="scope">{{ formatDate(scope.row.createdAt) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="260">
          <template #default="scope">
            <el-button size="small" @click="previewMaterial(scope.row)">预览</el-button>
            <el-button size="small" @click="downloadMaterial(scope.row)">下载</el-button>
            <el-button size="small" type="danger" @click="deleteMaterial(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else-if="!loading" description="暂无素材" />
      <p v-else role="status" aria-live="polite">正在加载素材…</p>
    </el-card>

    <el-dialog v-model="uploadDialogVisible" title="上传素材" width="560px">
      <el-upload
        v-model:file-list="uploadFiles"
        drag
        multiple
        :auto-upload="false"
        accept=".mp4,.mov,.m4v,.webm,.png,.jpg,.jpeg,.gif,.webp"
      >
        <div>将视频或图片拖到这里，或点击选择文件</div>
        <template #tip>
          <p>文件扩展名与媒体结构必须一致；损坏或伪装文件会被拒绝。</p>
        </template>
      </el-upload>
      <template #footer>
        <el-button @click="uploadDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="uploading"
          :disabled="uploading || uploadFiles.length === 0"
          @click="submitUpload"
        >上传所选素材</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="previewDialogVisible" title="素材预览" width="70%">
      <div v-if="currentMaterial" class="preview" role="region" aria-label="素材预览">
        <video
          v-if="isVideo(currentMaterial.name)"
          :src="materialApi.previewUrl(currentMaterial.id)"
          controls
        />
        <img
          v-else-if="isImage(currentMaterial.name)"
          :src="materialApi.previewUrl(currentMaterial.id)"
          :alt="currentMaterial.name"
        >
        <p v-else>该格式不支持内嵌预览，请下载查看。</p>
      </div>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { materialApi } from '@/api/material'

const materials = ref([])
const searchKeyword = ref('')
const loading = ref(false)
const uploading = ref(false)
const uploadDialogVisible = ref(false)
const previewDialogVisible = ref(false)
const uploadFiles = ref([])
const currentMaterial = ref(null)

const filteredMaterials = computed(() => {
  const query = searchKeyword.value.trim().toLowerCase()
  if (!query) return materials.value
  return materials.value.filter((material) => material.name.toLowerCase().includes(query))
})

const fetchMaterials = async () => {
  loading.value = true
  try {
    const response = await materialApi.getAllMaterials()
    materials.value = response.data.materials
  } finally {
    loading.value = false
  }
}

const submitUpload = async () => {
  if (!uploadFiles.value.length || uploading.value) return
  uploading.value = true
  let uploadedCount = 0
  try {
    for (const upload of uploadFiles.value) {
      if (!upload.raw) continue
      const formData = new FormData()
      formData.append('file', upload.raw)
      await materialApi.uploadMaterial(formData)
      uploadedCount += 1
    }
    uploadFiles.value = []
    uploadDialogVisible.value = false
    await fetchMaterials()
    ElMessage.success(`已上传 ${uploadedCount} 个素材`)
  } finally {
    uploading.value = false
  }
}

const previewMaterial = (material) => {
  currentMaterial.value = material
  previewDialogVisible.value = true
}

const downloadMaterial = (material) => {
  const link = document.createElement('a')
  link.href = materialApi.downloadUrl(material.id)
  link.download = material.name
  document.body.appendChild(link)
  link.click()
  link.remove()
}

const deleteMaterial = async (material) => {
  try {
    await ElMessageBox.confirm(
      `确定删除素材“${material.name}”吗？`,
      '删除素材',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  await materialApi.deleteMaterial(material.id)
  materials.value = materials.value.filter((item) => item.id !== material.id)
  ElMessage.success('素材已删除')
}

const formatBytes = (size) => {
  if (!Number.isFinite(size)) return '—'
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

const formatDate = (timestamp) => (
  Number.isFinite(timestamp) ? new Date(timestamp * 1000).toLocaleString() : '—'
)

const isVideo = (name) => /\.(mp4|mov|m4v|webm)$/i.test(name)
const isImage = (name) => /\.(png|jpe?g|gif|webp)$/i.test(name)

onMounted(fetchMaterials)
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 20px;

  h1 {
    margin: 0 0 8px;
    color: $text-primary;
  }

  p {
    margin: 0;
    color: $text-secondary;
  }
}

.header-actions {
  display: flex;
  flex-shrink: 0;
}

.search {
  width: min(380px, 100%);
  margin-bottom: 18px;
}

.preview {
  display: grid;
  min-height: 260px;
  place-items: center;
}

.preview video,
.preview img {
  max-width: 100%;
  max-height: 65vh;
}

@media (max-width: 720px) {
  .page-header {
    flex-direction: column;
  }
}
</style>
