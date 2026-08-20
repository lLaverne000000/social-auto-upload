<template>
  <section class="publish-center">
    <header class="page-header">
      <div>
        <h1>发布中心</h1>
        <p>发布任务由受治理的 CLI 核心执行；任务提交不代表发布成功。</p>
      </div>
      <el-button :loading="materialsLoading" @click="loadMaterials">刷新素材</el-button>
    </header>

    <el-alert
      class="governance-note"
      title="始终使用可见浏览器；默认需要人工确认；本地发布锁、冷却期和去重规则始终生效。本界面未新增每日发布数量限制。"
      type="info"
      :closable="false"
      show-icon
    />

    <el-card shadow="never">
      <el-form label-position="top" @submit.prevent="submitPublish">
        <div class="form-grid">
          <el-form-item label="平台" required>
            <el-select v-model="form.platform" :disabled="publishing">
              <el-option
                v-for="platform in platforms"
                :key="platform.value"
                :label="platform.label"
                :value="platform.value"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="账号别名" required>
            <el-input
              v-model="form.accountName"
              maxlength="80"
              placeholder="与账号登录页保持一致"
              :disabled="publishing"
            />
          </el-form-item>
        </div>

        <el-form-item label="素材" required>
          <el-select
            v-model="form.materialId"
            class="material-select"
            placeholder="请选择已上传素材"
            :disabled="publishing"
          >
            <el-option
              v-for="material in publishMaterials"
              :key="material.id"
              :label="`${material.name}（${formatBytes(material.sizeBytes)}）`"
              :value="material.id"
            />
          </el-select>
          <a
            v-if="selectedMaterial"
            class="preview-link"
            :href="materialApi.previewUrl(selectedMaterial.id)"
            target="_blank"
            rel="noopener"
          >预览素材</a>
        </el-form-item>

        <div class="quick-upload">
          <label for="publish-upload">没有素材？先上传到本机素材库</label>
          <input
            id="publish-upload"
            type="file"
            accept=".mp4,.mov,.m4v,.webm"
            :disabled="publishing || uploading"
            @change="chooseUpload"
          >
          <el-button
            :loading="uploading"
            :disabled="publishing || uploading || !uploadFile"
            @click="uploadMaterial"
          >上传并选中</el-button>
        </div>

        <el-form-item label="标题" required>
          <el-input
            v-model="form.title"
            maxlength="100"
            show-word-limit
            :disabled="publishing"
          />
        </el-form-item>

        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" :disabled="publishing" />
        </el-form-item>

        <el-form-item label="话题">
          <el-input
            v-model="form.tags"
            placeholder="多个话题用逗号分隔，不需要输入 #"
            :disabled="publishing"
          />
        </el-form-item>

        <el-form-item label="定时发布">
          <el-date-picker
            v-model="form.schedule"
            type="datetime"
            value-format="YYYY-MM-DD HH:mm"
            placeholder="留空表示立即发布"
            :disabled="publishing"
          />
        </el-form-item>

        <el-form-item v-if="form.platform === 'douyin'" label="抖音自主声明" required>
          <el-input
            v-model="form.declaration"
            placeholder="不声明时填写 none；声明时填写平台中的精确选项文字"
            :disabled="publishing"
          />
        </el-form-item>

        <el-form-item v-if="form.platform === 'xiaohongshu'" label="小红书内容来源" required>
          <el-radio-group v-model="form.contentSource" :disabled="publishing">
            <el-radio value="original">原创</el-radio>
          </el-radio-group>
          <span class="field-note">当前 GUI 视频流程仅支持原创；转载来源请使用 CLI 明确填写来源名称。</span>
        </el-form-item>

        <el-form-item v-if="supportsAutomaticPublish">
          <el-checkbox v-model="form.automaticPublish" :disabled="publishing">
            明确选择自动点击最终发布（默认关闭；开启后不再等待本次人工确认）
          </el-checkbox>
        </el-form-item>

        <el-button
          type="primary"
          native-type="submit"
          :loading="publishing"
          :disabled="publishing || uploading"
          @click="submitPublish"
        >
          {{ publishing ? '发布任务执行中' : '提交发布任务' }}
        </el-button>
      </el-form>
    </el-card>

    <el-card v-if="job" class="job-card" shadow="never">
      <div class="job-heading">
        <div>
          <h2>任务状态</h2>
          <p class="job-id">任务 ID：{{ job.id }}</p>
        </div>
        <el-tag :type="jobStatus.type" effect="dark">{{ jobStatus.label }}</el-tag>
      </div>
      <p role="status" aria-live="polite">{{ jobStatus.message }}</p>
      <el-button
        v-if="job.status === 'waiting-for-confirmation'"
        type="warning"
        :loading="confirming"
        :disabled="confirming"
        @click="confirmJob"
      >确认当前页面内容并继续发布</el-button>
    </el-card>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { materialApi } from '@/api/material'
import { formatBytes } from '@/utils/format'
import { createJobPoller } from '@/utils/jobPolling'
import { http } from '@/utils/request'

const platforms = [
  { value: 'xiaohongshu', label: '小红书' },
  { value: 'douyin', label: '抖音' },
  { value: 'kuaishou', label: '快手' },
  { value: 'tencent', label: '视频号' }
]

const JOB_STATUSES = {
  'queued': { label: '任务已排队', message: '任务已经进入本机队列，尚未发布。', type: 'info' },
  'running': { label: '正在执行发布', message: '可见浏览器正在执行发布流程。', type: 'primary' },
  'waiting-for-login': { label: '等待账号登录', message: '请在可见浏览器中完成账号登录。', type: 'warning' },
  'waiting-for-confirmation': { label: '等待你的发布确认', message: '请检查浏览器中的账号、素材、标题与声明，再点击下方确认按钮。', type: 'warning' },
  'succeeded': { label: '发布成功', message: '本次发布任务已由执行核心确认完成。', type: 'success' },
  'failed': { label: '发布失败', message: '本次任务未发布成功，请查看本地应用日志。', type: 'danger' },
  'blocked': { label: '已被本地安全控制阻止', message: '任务已停止，没有自动重试；请先处理风险提示或本地安全状态。', type: 'danger' }
}
const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'blocked'])

const form = reactive({
  platform: 'xiaohongshu',
  accountName: '',
  materialId: '',
  title: '',
  description: '',
  tags: '',
  schedule: '',
  declaration: 'none',
  contentSource: 'original',
  automaticPublish: false
})
const materials = ref([])
const materialsLoading = ref(false)
const uploadFile = ref(null)
const uploading = ref(false)
const publishing = ref(false)
const confirming = ref(false)
const job = ref(null)
const pollFailureMessage = ref('')

const selectedMaterial = computed(() => (
  publishMaterials.value.find((material) => material.id === form.materialId) || null
))
const publishMaterials = computed(() => (
  materials.value.filter((material) => /\.(mp4|mov|m4v|webm)$/i.test(material.name))
))
const supportsAutomaticPublish = computed(() => (
  ['douyin', 'xiaohongshu'].includes(form.platform)
))
const jobStatus = computed(() => {
  const status = JOB_STATUSES[job.value?.status] || {
    label: '未知状态',
    message: '本地服务返回了无法识别的任务状态。',
    type: 'info'
  }
  return {
    ...status,
    message: pollFailureMessage.value || status.message
  }
})

const applyJob = (nextJob) => {
  if (!nextJob || typeof nextJob.status !== 'string') return
  pollFailureMessage.value = ''
  job.value = nextJob
  if (TERMINAL_STATUSES.has(nextJob.status)) {
    publishing.value = false
    clearJobPoll()
  }
}

const jobPoller = createJobPoller({
  fetchJob: async (job) => {
    const response = await http.get(
      `/api/v1/jobs/${job.id}`,
      undefined,
      { silent: true }
    )
    return response.data
  },
  onJob: applyJob,
  onFailure: (message) => {
    pollFailureMessage.value = message
    job.value = { ...job.value, status: 'failed' }
    publishing.value = false
    ElMessage.error(message)
  }
})

const clearJobPoll = () => jobPoller.stop()
const scheduleJobPoll = () => jobPoller.start(job.value)

const loadMaterials = async () => {
  materialsLoading.value = true
  try {
    const response = await materialApi.getAllMaterials()
    materials.value = response.data.materials
    if (form.materialId && !publishMaterials.value.some((item) => item.id === form.materialId)) {
      form.materialId = ''
    }
  } finally {
    materialsLoading.value = false
  }
}

const chooseUpload = (event) => {
  uploadFile.value = event.target.files?.[0] || null
}

const uploadMaterial = async () => {
  if (!uploadFile.value || uploading.value) return
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', uploadFile.value)
    const response = await materialApi.uploadMaterial(formData)
    await loadMaterials()
    form.materialId = response.data.id
    uploadFile.value = null
    ElMessage.success('素材已上传并选中')
  } finally {
    uploading.value = false
  }
}

const publishPayload = () => ({
  platform: form.platform,
  accountName: form.accountName.trim(),
  materialId: form.materialId,
  title: form.title.trim(),
  tags: form.tags.split(/[,，]/).map((tag) => tag.trim().replace(/^#/, '')).filter(Boolean),
  description: form.description.trim(),
  schedule: form.schedule || null,
  declaration: form.platform === 'douyin' ? form.declaration.trim() : null,
  contentSource: form.platform === 'xiaohongshu' ? form.contentSource : null,
  automaticPublish: form.automaticPublish
})

const validatePublish = () => {
  if (!form.accountName.trim()) return '请输入账号别名'
  if (!form.materialId) return '请选择素材'
  if (!form.title.trim()) return '请输入标题'
  if (form.platform === 'xiaohongshu' && publishPayload().tags.length > 10) return '小红书话题最多 10 个'
  if (form.platform === 'douyin' && !form.declaration.trim()) return '请输入抖音自主声明，或填写 none'
  return ''
}

const submitPublish = async () => {
  if (publishing.value) return
  const validationError = validatePublish()
  if (validationError) {
    ElMessage.warning(validationError)
    return
  }
  clearJobPoll()
  publishing.value = true
  job.value = null
  pollFailureMessage.value = ''
  try {
    const response = await http.post('/api/v1/publish', publishPayload())
    applyJob(response.data)
    ElMessage.info('发布任务已提交，正在等待实际结果')
    if (!TERMINAL_STATUSES.has(response.data.status)) scheduleJobPoll()
  } catch {
    publishing.value = false
  }
}

const confirmPublishJob = (job) => (
  http.post(`/api/v1/jobs/${job.id}/confirm`)
)

const confirmJob = async () => {
  if (confirming.value || job.value?.status !== 'waiting-for-confirmation') return
  confirming.value = true
  const currentJob = job.value
  try {
    const response = await confirmPublishJob(currentJob)
    applyJob(response.data)
    if (!TERMINAL_STATUSES.has(response.data.status)) scheduleJobPoll()
  } finally {
    confirming.value = false
  }
}

watch(
  () => form.platform,
  () => {
    if (!supportsAutomaticPublish.value) form.automaticPublish = false
  }
)

onMounted(loadMaterials)
onBeforeUnmount(clearJobPoll)
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

.publish-center {
  max-width: 960px;
  margin: 0 auto;
}

.page-header,
.job-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.page-header {
  margin-bottom: 18px;

  h1,
  p {
    margin: 0;
  }

  h1 {
    margin-bottom: 8px;
    color: $text-primary;
  }

  p {
    color: $text-secondary;
  }
}

.governance-note {
  margin-bottom: 18px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.el-select,
.el-date-editor {
  width: 100%;
}

.material-select {
  flex: 1;
}

.preview-link {
  margin-left: 14px;
}

.field-note {
  margin-left: 12px;
  color: $text-secondary;
  font-size: 12px;
}

.quick-upload {
  display: grid;
  grid-template-columns: minmax(200px, 1fr) minmax(240px, 1fr) auto;
  align-items: center;
  gap: 14px;
  margin: 0 0 20px;
  padding: 14px;
  border: 1px dashed #dcdfe6;
  border-radius: 8px;
}

.job-card {
  margin-top: 18px;
}

.job-heading h2 {
  margin: 0 0 6px;
}

.job-id {
  margin: 0;
  color: $text-secondary;
  font-family: monospace;
  word-break: break-all;
}

@media (max-width: 720px) {
  .form-grid,
  .quick-upload {
    grid-template-columns: 1fr;
  }

  .page-header {
    flex-direction: column;
  }
}
</style>
