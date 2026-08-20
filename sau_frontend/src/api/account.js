import { http } from '@/utils/request'

export const accountApi = {
  login(platform, accountName) {
    return http.post('/api/v1/login', { platform, accountName })
  }
}
