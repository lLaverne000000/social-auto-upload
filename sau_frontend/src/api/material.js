import { http } from '@/utils/request'

const materialUrl = (id, action) => (
  `/api/v1/materials/${encodeURIComponent(id)}/${action}`
)

export const materialApi = {
  getAllMaterials() {
    return http.get('/api/v1/materials')
  },

  uploadMaterial(formData, onUploadProgress, config = {}) {
    return http.upload('/api/v1/materials', formData, onUploadProgress, config)
  },

  deleteMaterial(id) {
    return http.delete(`/api/v1/materials/${encodeURIComponent(id)}`)
  },

  previewUrl(id) {
    return materialUrl(id, 'preview')
  },

  downloadUrl(id) {
    return materialUrl(id, 'download')
  }
}
