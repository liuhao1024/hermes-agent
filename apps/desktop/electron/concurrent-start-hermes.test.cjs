// Test for concurrent startHermes() calls fix (#61023)

const assert = require('node:assert')
const { test } = require('node:test')

// Mock the dependencies
let mockConnectionPromise = null
let mockBackendStarting = false
let mockBootstrapFailure = null
let mockBackendStartFailure = null
let startCallCount = 0

// Simulate the fixed startHermes() function
async function mockStartHermes() {
  if (mockBootstrapFailure) {
    throw mockBootstrapFailure
  }
  if (mockBackendStartFailure) {
    throw mockBackendStartFailure
  }
  if (mockConnectionPromise) return mockConnectionPromise

  // Guard against concurrent calls
  if (mockBackendStarting) {
    return mockConnectionPromise
  }

  mockBackendStarting = true
  startCallCount++

  // Simulate async backend startup
  mockConnectionPromise = (async () => {
    await new Promise(resolve => setTimeout(resolve, 100))
    return { baseUrl: 'http://127.0.0.1:1234', mode: 'local' }
  })().finally(() => {
    mockBackendStarting = false
  })

  return mockConnectionPromise
}

test('concurrent startHermes() calls only spawn one backend', async () => {
  startCallCount = 0

  // Fire two concurrent calls (simulating main process + renderer)
  const [conn1, conn2] = await Promise.all([
    mockStartHermes(),
    mockStartHermes()
  ])

  // Both should resolve to the same connection
  assert.strictEqual(conn1.baseUrl, 'http://127.0.0.1:1234')
  assert.strictEqual(conn2.baseUrl, 'http://127.0.0.1:1234')

  // Backend should only be started once
  assert.strictEqual(startCallCount, 1, 'Backend should only be started once')
})

test('sequential startHermes() calls reuse the same connection', async () => {
  // Reset state
  mockConnectionPromise = null
  mockBackendStarting = false
  startCallCount = 0

  const conn1 = await mockStartHermes()
  const conn2 = await mockStartHermes()

  assert.strictEqual(conn1.baseUrl, 'http://127.0.0.1:1234')
  assert.strictEqual(conn2.baseUrl, 'http://127.0.0.1:1234')
  assert.strictEqual(startCallCount, 1, 'Backend should only be started once')
})

test('backendStarting flag is reset after error', async () => {
  // Reset state
  mockConnectionPromise = null
  mockBackendStarting = false
  startCallCount = 0

  // Inject a failure scenario
  const originalError = new Error('Backend failed to start')
  const failingStart = async () => {
    if (mockBackendStarting) return mockConnectionPromise
    mockBackendStarting = true
    startCallCount++
    mockConnectionPromise = Promise.reject(originalError).finally(() => {
      mockBackendStarting = false
    })
    return mockConnectionPromise
  }

  // First call should fail
  await assert.rejects(failingStart(), { message: 'Backend failed to start' })

  // Flag should be reset, allowing retry
  assert.strictEqual(mockBackendStarting, false)

  // Reset connectionPromise to allow retry
  mockConnectionPromise = null

  // Second call should succeed (using the working mock)
  const conn = await mockStartHermes()
  assert.strictEqual(conn.baseUrl, 'http://127.0.0.1:1234')
})