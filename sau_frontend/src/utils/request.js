import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
  baseURL: '/',
  withCredentials: true,
  headers: {
    Accept: 'application/json'
  }
})

const createApiError = ({ message, status, code, envelope, cause }) => {
  const apiError = new Error(message, cause ? { cause } : undefined)
  apiError.status = status
  apiError.code = code
  apiError.envelope = envelope
  return apiError
}

request.interceptors.response.use(
  (response) => {
    const envelope = response.data
    if (envelope?.ok === true) {
      return envelope
    }

    const message = envelope?.error?.message || '请求失败'
    if (response.config?.silent !== true) ElMessage.error(message)
    return Promise.reject(createApiError({
      message,
      status: response.status,
      code: envelope?.error?.code,
      envelope
    }))
  },
  (error) => {
    const envelope = error.response?.data
    const message = envelope?.error?.message
      || (error.response ? `请求失败（HTTP ${error.response.status}）` : '无法连接本地服务')
    if (error.config?.silent !== true) ElMessage.error(message)
    return Promise.reject(createApiError({
      message,
      status: error.response?.status,
      code: envelope?.error?.code || error.code,
      envelope,
      cause: error
    }))
  }
)

export const http = {
  get(url, params, config = {}) {
    return request.get(url, { ...config, params })
  },

  post(url, data, config = {}) {
    return request.post(url, data, config)
  },

  delete(url, config = {}) {
    return request.delete(url, config)
  },

  upload(url, formData, onUploadProgress, config = {}) {
    return request.post(url, formData, { ...config, onUploadProgress })
  }
}

export default request
