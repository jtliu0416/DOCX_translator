import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  withCredentials: true,
})

// --- Tasks ---

export function createTask(formData) {
  return api.post('/tasks', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function listTasks(page = 1, pageSize = 20) {
  return api.get('/tasks', { params: { page, page_size: pageSize } })
}

export function getTask(taskId) {
  return api.get(`/tasks/${taskId}`)
}

export function downloadTask(taskId) {
  return api.get(`/tasks/${taskId}/download`, { responseType: 'blob' })
}

export function deleteTask(taskId) {
  return api.delete(`/tasks/${taskId}`)
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
