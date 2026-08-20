import test from 'node:test'
import assert from 'node:assert/strict'

import { formatBytes } from '../src/utils/format.js'

test('formatBytes covers zero, kilobytes, and megabytes', () => {
  assert.equal(formatBytes(0), '0 B')
  assert.equal(formatBytes(1536), '1.5 KB')
  assert.equal(formatBytes(2.5 * 1024 * 1024), '2.50 MB')
})
