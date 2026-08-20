const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'blocked'])

const isDefinitiveError = (error) => {
  const status = error?.status
  return Number.isInteger(status)
    && status >= 400
    && status < 500
    && status !== 408
    && status !== 429
}

export const createJobPoller = ({
  fetchJob,
  onJob,
  onFailure,
  setTimer = (callback, delay) => window.setTimeout(callback, delay),
  clearTimer = (timer) => window.clearTimeout(timer),
  maxTransientFailures = 4,
  baseDelayMs = 1000,
  maxDelayMs = 8000
}) => {
  let timer = null
  let active = false
  let generation = 0
  let transientFailures = 0

  const stop = () => {
    active = false
    generation += 1
    if (timer !== null) {
      clearTimer(timer)
      timer = null
    }
  }

  const fail = (message) => {
    stop()
    onFailure(message)
  }

  const schedule = (job, token, delay) => {
    if (!active || token !== generation) return
    timer = setTimer(() => run(job, token), delay)
  }

  const run = async (job, token) => {
    timer = null
    if (!active || token !== generation) return
    try {
      const nextJob = await fetchJob(job)
      if (!active || token !== generation) return
      transientFailures = 0
      onJob(nextJob)
      if (TERMINAL_STATUSES.has(nextJob.status)) {
        stop()
        return
      }
      schedule(nextJob, token, baseDelayMs)
    } catch (error) {
      if (!active || token !== generation) return
      if (isDefinitiveError(error)) {
        fail(`任务状态请求失败（HTTP ${error.status}），已停止轮询。`)
        return
      }
      transientFailures += 1
      if (transientFailures >= maxTransientFailures) {
        fail(`连续 ${transientFailures} 次无法读取任务状态，已停止轮询。`)
        return
      }
      const delay = Math.min(
        baseDelayMs * (2 ** (transientFailures - 1)),
        maxDelayMs
      )
      schedule(job, token, delay)
    }
  }

  const start = (job) => {
    stop()
    if (!job?.id || TERMINAL_STATUSES.has(job.status)) return
    active = true
    transientFailures = 0
    const token = generation
    schedule(job, token, baseDelayMs)
  }

  return { start, stop }
}
