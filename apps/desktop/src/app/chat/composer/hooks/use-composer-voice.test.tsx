import { act, cleanup, renderHook } from '@testing-library/react'
import type * as Nanostores from 'nanostores'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useComposerVoice } from './use-composer-voice'

const mocks = vi.hoisted(() => ({
  activeTarget: 'tile:test' as string,
  clearDraft: vi.fn(),
  conversationEnabled: [] as boolean[],
  conversationSubmits: [] as Array<(text: string) => Promise<void>>,
  markActiveComposer: vi.fn((target: string) => {
    mocks.activeTarget = target
  }),
  onSubmit: vi.fn(async () => true),
  resetBrowseState: vi.fn(),
  triggerHaptic: vi.fn()
}))

vi.mock('@nanostores/react', () => ({
  useStore: (store: unknown) => store
}))

vi.mock('nanostores', async () => {
  const actual = await vi.importActual<typeof Nanostores>('nanostores')

  return { ...actual, computed: (deps: unknown, fn: unknown) => fn }
})

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      assistant: { thread: { readAloudFailed: '' } },
      notifications: { voice: { sayStopToEnd: () => '' } },
      settings: { config: { autosaveFailed: '' } }
    }
  })
}))

vi.mock('@/lib/haptics', () => ({
  triggerHaptic: mocks.triggerHaptic
}))

vi.mock('@/lib/spoken-reply', () => ({
  adoptSpokenReplySession: vi.fn(),
  markAssistantIdSpoken: vi.fn(),
  resolveSpokenReply: vi.fn(() => null)
}))

vi.mock('@/lib/tts-lease', () => ({
  CONVERSATION_LEASE: 'conversation',
  READ_ALOUD_LEASE: 'read-aloud',
  syncTtsLease: vi.fn(async () => undefined)
}))

vi.mock('@/lib/wake-indicator', () => ({
  clearWakeIndicator: vi.fn(),
  syncWakeIndicatorWithVoice: vi.fn(() => false)
}))

vi.mock('@/store/composer', () => ({
  $voiceConversationStartRequest: { get: () => null },
  takeVoiceConversationStart: vi.fn(() => false)
}))

vi.mock('@/store/composer-input-history', () => ({
  resetBrowseState: mocks.resetBrowseState
}))

vi.mock('@/store/gateway', () => ({
  $gateway: { get: () => null }
}))

vi.mock('@/store/notifications', () => ({
  notify: vi.fn(),
  notifyError: vi.fn()
}))

vi.mock('@/store/voice-prefs', () => ({
  $autoSpeakReplies: { get: () => false },
  $voiceStopPhrase: { get: () => null },
  setAutoSpeakReplies: vi.fn(async () => undefined)
}))

vi.mock('@/store/wake-word', () => ({
  resumeWakeAfterVoice: vi.fn(async () => undefined)
}))

vi.mock('../focus', () => ({
  getActiveComposer: () => mocks.activeTarget,
  markActiveComposer: mocks.markActiveComposer,
  onComposerDictationRequest: vi.fn(() => () => undefined),
  onComposerVoiceToggleRequest: vi.fn(() => () => undefined)
}))

vi.mock('../scope', () => ({
  useComposerScope: () => ({ $messages: { get: () => [] } }),
  useComposerSurfaceId: () => null
}))

vi.mock('./use-auto-speak-replies', () => ({
  useAutoSpeakReplies: vi.fn()
}))

vi.mock('./use-voice-conversation', () => ({
  useVoiceConversation: ({
    enabled,
    onSubmit
  }: {
    enabled: boolean
    onSubmit: (text: string) => Promise<void>
  }) => {
    mocks.conversationSubmits.push(onSubmit)
    mocks.conversationEnabled.push(enabled)

    return {
      end: vi.fn(async () => undefined),
      level: 0,
      muted: false,
      start: vi.fn(async () => undefined),
      status: 'idle',
      stopTurn: vi.fn(),
      toggleMute: vi.fn()
    }
  }
}))

vi.mock('./use-voice-recorder', () => ({
  useVoiceRecorder: () => ({
    dictate: vi.fn(),
    voiceActivityState: { elapsedSeconds: 0, level: 0, status: 'idle' },
    voiceStatus: 'idle'
  })
}))

describe('useComposerVoice voice submission', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.clearAllMocks()
    mocks.conversationSubmits.length = 0
    mocks.conversationEnabled.length = 0
    mocks.activeTarget = 'tile:test'
  })

  it('uses live busy state when a retained voice callback submits after barge-in', async () => {
    const hook = renderHook(
      ({ busy }) =>
        useComposerVoice({
          busy,
          clearDraft: mocks.clearDraft,
          disabled: false,
          focusInput: vi.fn(),
          insertText: vi.fn(),
          maxRecordingSeconds: 60,
          onSubmit: mocks.onSubmit,
          onTranscribeAudio: vi.fn(async () => ''),
          sessionId: 'session-1',
          target: 'tile:test'
        }),
      { initialProps: { busy: true } }
    )

    const retainedSubmit = mocks.conversationSubmits[0]

    hook.rerender({ busy: false })
    await act(async () => retainedSubmit('change direction'))

    expect(mocks.onSubmit).toHaveBeenCalledWith('change direction', { voiceTurn: true })
    expect(mocks.clearDraft).toHaveBeenCalledTimes(1)
    expect(mocks.resetBrowseState).toHaveBeenCalledWith('session-1')
    expect(mocks.triggerHaptic).toHaveBeenCalledWith('submit')

    hook.rerender({ busy: true })
    await act(async () => retainedSubmit('do not double-submit'))

    expect(mocks.onSubmit).toHaveBeenCalledTimes(1)
    expect(mocks.clearDraft).toHaveBeenCalledTimes(1)
  })
})

describe('useComposerVoice conversation targeting (#140954)', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.clearAllMocks()
    mocks.conversationSubmits.length = 0
    mocks.conversationEnabled.length = 0
    mocks.activeTarget = 'tile:test'
  })

  const renderVoiceHook = () =>
    renderHook(() =>
      useComposerVoice({
        busy: false,
        clearDraft: mocks.clearDraft,
        disabled: false,
        focusInput: vi.fn(),
        insertText: vi.fn(),
        maxRecordingSeconds: 60,
        onSubmit: mocks.onSubmit,
        onTranscribeAudio: vi.fn(async () => ''),
        sessionId: 'session-1',
        target: 'tile:test'
      })
    )

  it('claims the voice target when the conversation starts, then drops and regains it with the focus bus', async () => {
    vi.useFakeTimers()
    // Split view: another pane held the bus before voice started here.
    mocks.activeTarget = 'tile:other'

    const hook = renderVoiceHook()

    expect(mocks.conversationEnabled.at(-1)).toBe(false)

    act(() => hook.result.current.startConversation())

    // Starting voice marks THIS composer as the one voice is aimed at, so the
    // conversation is enabled on its first frame instead of gating itself off.
    expect(mocks.markActiveComposer).toHaveBeenCalledWith('tile:test')
    expect(mocks.conversationEnabled.at(-1)).toBe(true)

    // Switch to another session: the backgrounded conversation must stop
    // listening (enabled=false drives the conversation's end/mic teardown).
    mocks.activeTarget = 'tile:other'
    await act(async () => vi.advanceTimersByTimeAsync(600))

    expect(mocks.conversationEnabled.at(-1)).toBe(false)

    // Switch back: the same conversation re-arms without a fresh start request.
    mocks.activeTarget = 'tile:test'
    await act(async () => vi.advanceTimersByTimeAsync(600))

    expect(mocks.conversationEnabled.at(-1)).toBe(true)
  })

  it('keeps a never-started composer out of the voice loop', async () => {
    vi.useFakeTimers()
    mocks.activeTarget = 'tile:other'

    const hook = renderVoiceHook()

    await act(async () => vi.advanceTimersByTimeAsync(1200))

    // No conversation requested here — this composer never listens, whatever
    // the bus says, so it cannot transcribe speech meant for the other pane.
    expect(mocks.conversationEnabled.every(enabled => enabled === false)).toBe(true)
    expect(hook.result.current.voiceConversationActive).toBe(false)
  })
})
