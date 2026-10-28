import { describe, expect, it } from 'vitest'

import { wrapText } from './text'

// Deterministic measureText stand-in: every code point costs the same width,
// so wrap positions are exact regardless of platform font metrics.
function fakeCtx(widthPerChar: number): CanvasRenderingContext2D {
  return {
    measureText(s: string) {
      return { width: [...s].length * widthPerChar }
    },
  } as unknown as CanvasRenderingContext2D
}

describe('wrapText', () => {
  it('wraps a continuous CJK label inside the width budget without losing text', () => {
    const ctx = fakeCtx(10)
    const label = '中文标签应当在画布提示范围内完整可读'.repeat(3)
    const maxW = 368

    const lines = wrapText(ctx, label, maxW)

    expect(lines.length).toBeGreaterThan(1)

    for (const line of lines) {
      expect(ctx.measureText(line).width).toBeLessThanOrEqual(maxW)
    }

    expect(lines.join('')).toBe(label)
  })

  it('keeps space-separated words whole while filling each line', () => {
    const ctx = fakeCtx(10)
    const text = 'alpha beta gamma delta epsilon zeta'

    const lines = wrapText(ctx, text, 120)

    expect(lines.length).toBeGreaterThan(1)
    expect(lines.join(' ')).toBe(text)

    for (const line of lines) {
      expect(ctx.measureText(line).width).toBeLessThanOrEqual(120)
    }
  })

  it('flushes the pending line before segmenting an oversized CJK token', () => {
    const ctx = fakeCtx(10)
    const cjk = '中文标签连续不断开直到超出预算'
    const text = `short ${cjk}`

    const lines = wrapText(ctx, text, 120)

    expect(lines[0]).toBe('short')
    // 12 glyphs fit per line at 10px each; the rest moves to the next line.
    expect(lines.slice(1).join('')).toBe(cjk)

    for (const line of lines.slice(1)) {
      expect(ctx.measureText(line).width).toBeLessThanOrEqual(120)
    }
  })

  it('breaks an oversized emoji run at grapheme boundaries, not UTF-16 units', () => {
    const ctx = fakeCtx(10)
    const unit = '👍🏽' // 2 code points, 1 grapheme
    const label = unit.repeat(20)
    const maxW = 250
    const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' })

    const lines = wrapText(ctx, label, maxW)

    expect(lines.length).toBeGreaterThan(1)
    expect(lines.join('')).toBe(label)

    for (const line of lines) {
      expect(ctx.measureText(line).width).toBeLessThanOrEqual(maxW)

      for (const { segment } of segmenter.segment(line)) {
        expect(segment).toBe(unit)
      }
    }
  })

  it('still preserves text when one grapheme exceeds the whole budget', () => {
    const ctx = fakeCtx(100)

    const lines = wrapText(ctx, '中文中文', 50)

    expect(lines).toEqual(['中', '文', '中', '文'])
  })
})
