import { act, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import { chatMessageText } from '@/lib/chat-messages'

import { type MessageStreamHarness, renderMessageStream } from './test-harness'
import { STREAM_DELTA_FLUSH_MS } from './utils'

// A turn whose first segment is commentary + a tool call, and whose final
// answer is *different* text, painted the commentary twice when a
// message.start landed between the interim seal and the re-stream (#144609):
// the first copy materialized its own standalone bubble (no live bubble
// existed), the new occurrence's message.start kept the messages and reset
// interimBoundaryPending, and the re-streamed copy then sealed the live
// bubble in place — the in-place seal never asked whether this occurrence
// already paints the same text, unlike the no-live-bubble path beside it.
//
// The twin with the interim text equal to the FINAL text across a boundary
// must still paint twice (interim-redelivery-duplicate.test.tsx); the
// complete path is untouched here, so that contract holds.
const SID = 'dup-interim-boundary'

const COMMENTARY =
  "For fidelity the decisive knob isn't calibration — it's `lm_head`. Let me verify what each checkpoint does with it."

const FINAL = 'Verified: the two checkpoints differ in lm_head only.'

let stream: MessageStreamHarness

const mountStream = () => {
  vi.useFakeTimers()
  stream = renderMessageStream(SID)
}

const start = () => act(() => stream.handleEvent({ payload: {}, session_id: SID, type: 'message.start' }))

const delta = (text: string) =>
  act(() => stream.handleEvent({ payload: { text }, session_id: SID, type: 'message.delta' }))

const interim = (text: string) =>
  act(() => stream.handleEvent({ payload: { text, already_streamed: true }, session_id: SID, type: 'message.interim' }))

const toolStart = () =>
  act(() =>
    stream.handleEvent({
      payload: { args: { command: 'ls' }, name: 'terminal', tool_id: 'call-1' },
      session_id: SID,
      type: 'tool.start'
    })
  )

const complete = (text: string) =>
  act(() => stream.handleEvent({ payload: { text }, session_id: SID, type: 'message.complete' }))

const flushDeltas = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(STREAM_DELTA_FLUSH_MS + 10)
  })

const state = (): ClientSessionState => stream.state()

/** Every visible assistant bubble's text, in transcript order. */
const visibleAssistantTexts = (): string[] =>
  state()
    .messages.filter(message => message.role === 'assistant' && !message.hidden)
    .map(message => chatMessageText(message).trim())
    .filter(Boolean)

describe('commentary re-streamed across a message.start boundary paints once', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('collapses the re-streamed interim copy when the first occurrence sealed its own bubble', async () => {
    mountStream()
    await start()
    await interim(COMMENTARY)
    await start()
    await delta(COMMENTARY)
    await flushDeltas()
    await interim(COMMENTARY)
    await toolStart()
    await delta(FINAL)
    await flushDeltas()
    await complete(FINAL)

    expect(visibleAssistantTexts()).toEqual([COMMENTARY, FINAL])
  })

  it('keeps the sealed bubble carrying its tool rows when the live bubble has more than text', async () => {
    mountStream()
    await start()
    await interim(COMMENTARY)
    await start()
    await delta(COMMENTARY)
    await flushDeltas()
    await toolStart()
    await interim(COMMENTARY)

    // The live bubble survived the seal (it owns the tool row) and no longer
    // repeats the commentary; the first occurrence's bubble is untouched.
    expect(visibleAssistantTexts()).toEqual([COMMENTARY])
    expect(state().messages.some(message => message.id === state().streamId)).toBe(false)

    const bubblesWithTools = state().messages.filter(message =>
      message.parts.some(part => part.type === 'tool-call')
    )

    expect(bubblesWithTools).toHaveLength(1)
  })
})
