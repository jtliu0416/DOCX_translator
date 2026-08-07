import axios from 'axios'
import { getAccessToken } from '../auth'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
})

api.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// --- Tasks ---

export function createTask(formData) {
  return api.post('/tasks', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function createTaskUpload(payload) {
  return api.post('/tasks/uploads', payload)
}

export function uploadTaskChunk(uploadId, formData, onUploadProgress) {
  return api.post(`/tasks/uploads/${uploadId}/chunks`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress,
  })
}

export function completeTaskUpload(uploadId) {
  return api.post(`/tasks/uploads/${uploadId}/complete`, { upload_id: uploadId })
}

export function listTasks(page = 1, pageSize = 20) {
  return api.get('/tasks', { params: { page, page_size: pageSize } })
}

export function getTaskStatistics() {
  return api.get('/tasks/statistics')
}

export function getTask(taskId) {
  return api.get(`/tasks/${taskId}`)
}

export function downloadTask(taskId) {
  return api.get(`/tasks/${taskId}/download`, { responseType: 'blob' })
}

export function batchDownloadTasks(taskIds) {
  return api.post('/tasks/batch-download', { task_ids: taskIds }, { responseType: 'blob' })
}

export function deleteTask(taskId) {
  return api.delete(`/tasks/${taskId}`)
}

export function retryTask(taskId) {
  return api.post(`/tasks/${taskId}/retry`)
}

// --- Glossaries ---

export function createGlossary(formData) {
  return api.post('/glossaries', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function listGlossaries() {
  return api.get('/glossaries')
}

export function getGlossary(id) {
  return api.get(`/glossaries/${id}`)
}

export function deleteGlossary(id) {
  return api.delete(`/glossaries/${id}`)
}

// --- Languages ---

export function listLanguages() {
  return api.get('/languages')
}

// --- Settings (read-only from .env) ---

export function getLLMSettings() {
  return api.get('/settings/llm')
}

export function getUploadSettings() {
  return api.get('/settings/upload')
}

export function getConcurrencySettings() {
  return api.get('/settings/concurrency')
}
