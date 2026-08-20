import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
  baseURL: '/',
  withCredentials: true,
  headers: {
    Accept: 'application/json'
  }
})

request.interceptors.response.use(
  (response) => {
    const envelope = response.data
    if (envelope?.ok === true) {
      return envelope
    }

    const message = envelope?.error?.message || '请求失败'
    ElMessage.error(message)
    return Promise.reject(new Error(message))
  },
  (error) => {
    const message = error.response?.data?.error?.message
      || (error.response ? `请求失败（HTTP ${error.response.status}）` : '无法连接本地服务')
    ElMessage.error(message)
    return Promise.reject(new Error(message, { cause: error }))
  }
)

export const http = {
  get(url, params) {
    return request.get(url, { params })
  },

  post(url, data, config = {}) {
    return request.post(url, data, config)
  },

  delete(url, config = {}) {
    return request.delete(url, config)
  },

  upload(url, formData, onUploadProgress) {
    return request.post(url, formData, { onUploadProgress })
  }
}

export default request
