import { describe, expect, it } from 'vitest'

import { normalizeFilePreviewMath, preprocessMarkdown } from '@/lib/markdown-preprocess'

// Real assistant math labeled the standard LaTeX way — CJK inside `\text{}`
// — must stay a math span (#143506). Taken verbatim from the report, the
// second line being the sharpest shape: a single span alone on its line.
const TEXT_LABEL_LINES = [
  '设外圆围成的圆盘是 $D_{\\text{外}}$，内圆围成的圆盘是 $D_{\\text{内}}$。',
  '设圆盘是 $D_{\\text{外}}$。',
  '对圆环 $A=D_{\\text{外}}-D_{\\text{内}}$ 用格林：',
  '外圆逆时针：边界是 $+\\partial D_{\\text{外}}$，对应 $+D_{\\text{外}}$。'
]

describe('CJK prose dollar escaping', () => {
  it('keeps inline math whose CJK sits inside \\text{} runs unescaped', () => {
    for (const line of TEXT_LABEL_LINES) {
      const output = preprocessMarkdown(line)

      expect(output).toBe(line)
      expect(output).not.toContain('\\$')
    }
  })

  it('exempts the same spans in file previews and tolerates `\\text {外}` spacing', () => {
    expect(normalizeFilePreviewMath('圆盘 $D_{\\text{外}}$。')).toBe('圆盘 $D_{\\text{外}}$。')
    expect(preprocessMarkdown('圆盘 $D_{\\text {外}}$。')).toBe('圆盘 $D_{\\text {外}}$。')
  })

  it('still escapes bare CJK spans, including one alone on its line', () => {
    expect(preprocessMarkdown('半径是 $x = 变量$ 请代入。')).toBe('半径是 \\$x = 变量$ 请代入。')
    expect(preprocessMarkdown('价格是 $这个$。')).toBe('价格是 \\$这个$。')
  })

  it('escapes when CJK appears outside the \\text{} run — the prose signal survives', () => {
    expect(preprocessMarkdown('混合 $D_{\\text{外}} + 变$。')).toBe('混合 \\$D_{\\text{外}} + 变$。')
  })

  it('leaves CJK-free inline math and display math with CJK bodies untouched', () => {
    expect(preprocessMarkdown('$E = mc^2$ 代入 $x$ 求解')).toBe('$E = mc^2$ 代入 $x$ 求解')
    expect(preprocessMarkdown('和 $$D_{\\text{外}}-D_{\\text{内}},$$ 得。')).toBe(
      '和 $$D_{\\text{外}}-D_{\\text{内}},$$ 得。'
    )
  })
})
