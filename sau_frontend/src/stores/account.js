import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAccountStore = defineStore('account', () => {
  const recentLogins = ref([])

  const rememberLogin = (platform, accountName) => {
    recentLogins.value = [
      { platform, accountName },
      ...recentLogins.value.filter(
        (item) => item.platform !== platform || item.accountName !== accountName
      )
    ].slice(0, 10)
  }

  return { recentLogins, rememberLogin }
})
