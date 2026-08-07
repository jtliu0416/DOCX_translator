const TOKEN_KEY = 'doctrans.webui.jwt'
const WEBUI_ORIGINS = (import.meta.env.VITE_WEBUI_ORIGINS || import.meta.env.VITE_WEBUI_ORIGIN || '')
  .split(',')
  .map(origin => origin.trim().replace(/\/$/, ''))
  .filter(Boolean)

export function getAccessToken() {
  return sessionStorage.getItem(TOKEN_KEY) || ''
}

function createNonce() {
  const browserCrypto = window.crypto || window.msCrypto
  if (!browserCrypto) throw new Error('当前浏览器不支持安全随机数')
  if (typeof browserCrypto.randomUUID === 'function') return browserCrypto.randomUUID()

  const bytes = new Uint8Array(16)
  browserCrypto.getRandomValues(bytes)
  return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
}

function isExpectedMessage(event, nonce) {
  return Boolean(
    WEBUI_ORIGINS.includes(event.origin) &&
    event.source === window.opener &&
    event.data &&
    event.data.type === 'doctrans:auth' &&
    event.data.nonce === nonce &&
    typeof event.data.token === 'string' &&
    event.data.token.trim()
  )
}

export function requestWebUiToken() {
  if (getAccessToken() || WEBUI_ORIGINS.length === 0 || !window.opener) return Promise.resolve()

  return new Promise((resolve) => {
    const nonce = createNonce()
    const timeout = window.setTimeout(cleanup, 5000)
    const receiveToken = (event) => {
      if (!isExpectedMessage(event, nonce)) return
      sessionStorage.setItem(TOKEN_KEY, event.data.token.trim())
      cleanup()
      window.dispatchEvent(new Event('doctrans:authenticated'))
    }
    function cleanup() {
      window.clearTimeout(timeout)
      window.removeEventListener('message', receiveToken)
      resolve()
    }

    window.addEventListener('message', receiveToken)
    WEBUI_ORIGINS.forEach((origin) => {
      window.opener.postMessage({ type: 'doctrans:ready', nonce }, origin)
    })
  })
}
