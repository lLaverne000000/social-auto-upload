import test from 'node:test'
import assert from 'node:assert/strict'

import { createJobPoller } from '../src/utils/jobPolling.js'

class FakeTimers {
  constructor() {
    this.nextId = 1
    this.tasks = new Map()
    this.delays = []
  }

  set = (callback, delay) => {
    const id = this.nextId++
    this.tasks.set(id, callback)
    this.delays.push(delay)
    return id
  }

  clear = (id) => {
    this.tasks.delete(id)
  }

  async runNext() {
    const entry = this.tasks.entries().next().value
    assert.ok(entry, 'expected a scheduled poll')
    const [id, callback] = entry
    this.tasks.delete(id)
    await callback()
  }
}

const errorWithStatus = (status) => Object.assign(new Error(`HTTP ${status}`), { status })

test('a definitive polling error fails immediately and releases the job', async () => {
  const timers = new FakeTimers()
  const failures = []
  const poller = createJobPoller({
    fetchJob: async () => { throw errorWithStatus(404) },
    onJob: () => assert.fail('404 must not produce a job update'),
    onFailure: (message) => failures.push(message),
    setTimer: timers.set,
    clearTimer: timers.clear
  })

  poller.start({ id: 'missing', status: 'queued' })
  await timers.runNext()

  assert.equal(failures.length, 1)
  assert.match(failures[0], /HTTP 404/)
  assert.equal(timers.tasks.size, 0)
})

test('transient polling failures have a bounded exponential backoff', async () => {
  const timers = new FakeTimers()
  const failures = []
  const poller = createJobPoller({
    fetchJob: async () => { throw new Error('offline') },
    onJob: () => assert.fail('offline requests must not produce a job update'),
    onFailure: (message) => failures.push(message),
    setTimer: timers.set,
    clearTimer: timers.clear,
    maxTransientFailures: 4,
    baseDelayMs: 100
  })

  poller.start({ id: 'offline', status: 'running' })
  await timers.runNext()
  await timers.runNext()
  await timers.runNext()
  await timers.runNext()

  assert.deepEqual(timers.delays, [100, 100, 200, 400])
  assert.equal(failures.length, 1)
  assert.match(failures[0], /连续 4 次/)
  assert.equal(timers.tasks.size, 0)
})

test('a successful response resets failures and stop cancels stale work', async () => {
  const timers = new FakeTimers()
  const responses = [
    () => { throw new Error('first outage') },
    () => ({ id: 'job', status: 'running' }),
    () => { throw new Error('second outage') }
  ]
  const jobs = []
  const poller = createJobPoller({
    fetchJob: async () => responses.shift()(),
    onJob: (job) => jobs.push(job),
    onFailure: () => assert.fail('isolated outages remain retryable'),
    setTimer: timers.set,
    clearTimer: timers.clear,
    maxTransientFailures: 4,
    baseDelayMs: 100
  })

  poller.start({ id: 'job', status: 'queued' })
  await timers.runNext()
  await timers.runNext()
  await timers.runNext()

  assert.equal(jobs.length, 1)
  assert.deepEqual(timers.delays, [100, 100, 100, 100])
  assert.equal(timers.tasks.size, 1)
  poller.stop()
  assert.equal(timers.tasks.size, 0)
})
