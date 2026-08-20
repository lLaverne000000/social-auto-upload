<template>
  <section class="account-management">
    <header class="page-header">
      <div>
        <h1>账号登录</h1>
        <p>启动平台的可见浏览器，完成扫码或账号验证后再回到这里。</p>
      </div>
    </header>

    <el-card class="login-card" shadow="never">
      <el-form label-position="top" @submit.prevent="startLogin">
        <el-form-item label="平台" required>
          <el-select v-model="form.platform" placeholder="请选择平台" :disabled="sseConnecting">
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
            placeholder="例如 creator-a"
            autocomplete="off"
            :disabled="sseConnecting"
            @keyup.enter="startLogin"
          />
          <p class="field-help">发布中心必须使用同一个账号别名。</p>
        </el-form-item>

        <el-button
          type="primary"
          native-type="submit"
          :loading="sseConnecting"
          :disabled="sseConnecting || !canSubmit"
          @click="startLogin"
        >
          {{ sseConnecting ? '等待浏览器登录' : '启动登录' }}
        </el-button>
      </el-form>

      <el-alert
        v-if="loginJob"
        class="login-status"
        :title="loginStatusMessage"
        :type="loginStatusType"
        :closable="false"
        show-icon
        role="status"
        aria-live="polite"
      >
        <p v-if="sseConnecting">请在新打开的可见浏览器中完成平台登录，窗口不要提前关闭。</p>
      </el-alert>
    </el-card>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { http } from '@/utils/request'

const platforms = [
  { value: 'xiaohongshu', label: '小红书' },
  { value: 'douyin', label: '抖音' },
  { value: 'kuaishou', label: '快手' },
  { value: 'tencent', label: '视频号' }
]

const statusMessages = {
  queued: '登录任务已排队',
  'waiting-for-login': '等待你在浏览器中完成登录',
  running: '正在准备登录浏览器',
  succeeded: '登录完成',
  failed: '登录失败，请查看本地应用日志',
  blocked: '登录已被本地服务阻止'
}

const form = reactive({
  platform: 'xiaohongshu',
  accountName: ''
})
const loginJob = ref(null)
const sseConnecting = ref(false)
let eventSource = null

const canSubmit = computed(() => form.platform && form.accountName.trim())
const loginStatusMessage = computed(() => (
  statusMessages[loginJob.value?.status] || '正在读取登录状态'
))
const loginStatusType = computed(() => {
  if (loginJob.value?.status === 'succeeded') return 'success'
  if (['failed', 'blocked'].includes(loginJob.value?.status)) return 'error'
  return 'info'
})

const closeSSEConnection = () => {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

const applyLoginStatus = (nextJob) => {
  if (!nextJob || typeof nextJob.status !== 'string') return
  loginJob.value = nextJob
  if (['succeeded', 'failed', 'blocked'].includes(nextJob.status)) {
    sseConnecting.value = false
    closeSSEConnection()
    if (nextJob.status === 'succeeded') {
      ElMessage.success('账号登录完成')
    }
  }
}

const connectSSE = (jobId) => {
  closeSSEConnection()
  eventSource = new EventSource(`/api/v1/login/${jobId}/events`)
  eventSource.addEventListener('status', (event) => {
    try {
      const envelope = JSON.parse(event.data)
      if (envelope?.ok === true) applyLoginStatus(envelope.data)
    } catch {
      sseConnecting.value = false
      closeSSEConnection()
      ElMessage.error('登录状态响应无法解析')
    }
  })
  eventSource.onerror = () => {
    if (!['succeeded', 'failed', 'blocked'].includes(loginJob.value?.status)) {
      sseConnecting.value = false
      ElMessage.error('登录状态连接已中断')
    }
    closeSSEConnection()
  }
}

const startLogin = async () => {
  if (sseConnecting.value || !canSubmit.value) return
  sseConnecting.value = true
  loginJob.value = null
  try {
    const response = await http.post('/api/v1/login', {
      platform: form.platform,
      accountName: form.accountName.trim()
    })
    applyLoginStatus(response.data)
    if (response.data?.id && sseConnecting.value) {
      connectSSE(response.data.id)
    }
  } catch {
    sseConnecting.value = false
  }
}

onBeforeUnmount(closeSSEConnection)
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

.account-management {
  max-width: 760px;
  margin: 0 auto;
}

.page-header {
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

.login-card {
  border-radius: 10px;
}

.el-select {
  width: 100%;
}

.field-help {
  margin: 6px 0 0;
  color: $text-secondary;
  font-size: 12px;
}

.login-status {
  margin-top: 20px;
}
</style>
