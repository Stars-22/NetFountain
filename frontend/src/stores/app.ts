import { defineStore } from 'pinia'
import { ref } from 'vue'

const KEY = 'netfountain-dark'

export const useAppStore = defineStore('app', () => {
  const dark = ref(typeof localStorage !== 'undefined' && localStorage.getItem(KEY) === '1')

  function apply() {
    document.documentElement.classList.toggle('dark', dark.value)
  }

  function setDark(v: boolean) {
    dark.value = v
    localStorage.setItem(KEY, v ? '1' : '0')
    apply()
  }

  return { dark, apply, setDark }
})
