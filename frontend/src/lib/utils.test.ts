import { describe, expect, it } from 'vitest'

import { cn, formatTime } from './utils'

describe('frontend utility contracts', () => {
  it('merges conditional Tailwind classes without duplicate conflicts', () => {
    expect(cn('px-2 text-muted', true && 'px-4', false && 'hidden')).toContain('px-4')
    expect(cn('px-2 text-muted', true && 'px-4')).not.toContain('px-2')
  })

  it('formats valid timestamps and safely ignores invalid values', () => {
    expect(formatTime(undefined)).toBe('')
    expect(formatTime('not-a-date')).toBe('')
    expect(formatTime('2026-01-02T03:04:00.000Z')).toMatch(/\d{2}[/:]\d{2}/)
  })
})
