import { AssistantRuntimeProvider, type ThreadMessageLike, useExternalStoreRuntime } from '@assistant-ui/react'
import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { en } from '@/i18n/en'
import { mainComposerScope } from '@/store/composer'
import { clearApprovalRequest, setApprovalRequest } from '@/store/prompts'
import { $sessionStates } from '@/store/session-states'

import { RICH_INPUT_SLOT } from './rich-editor'
import type { ChatBarState } from './types'

import { ChatBar } from './index'

afterEach(() => {
  cleanup()
  clearApprovalRequest()
  $sessionStates.set({})
  mainComposerScope.clear()
  localStorage.clear()
})

const chatBarState: ChatBarState = {
  model: { canSwitch: false, model: '', provider: '' },
  tools: { enabled: false, label: '' },
  voice: { enabled: false, active: false }
}

function Harness({ busy, onCancel }: { busy: boolean; onCancel: () => void }) {
  const runtime = useExternalStoreRuntime({
    convertMessage: (message: ThreadMessageLike) => message,
    isRunning: busy,
    messages: [] as ThreadMessageLike[],
    onNew: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <MemoryRouter>
        <I18nProvider configClient={null} initialLocale="en">
          <ChatBar
            busy={busy}
            disabled={false}
            freshDraftKey="__new__:fresh"
            gateway={null}
            onCancel={onCancel}
            onSubmit={vi.fn(async () => true)}
            queueSessionKey={null}
            sessionId="session-a"
            state={chatBarState}
          />
        </I18nProvider>
      </MemoryRouter>
    </AssistantRuntimeProvider>
  )
}

/** The bar's visible surface, and the truly empty area a pointer click lands
 *  on when the user means "the bar", not any control in it. The `await act`
 *  lets the AUI composer core bind (an effect), so text staged in the editor
 *  below actually reaches `hasComposerPayload`. */
async function mount(busy: boolean, onCancel: () => void) {
  const view = render(<Harness busy={busy} onCancel={onCancel} />)
  await act(async () => {})

  const surface = view.container.querySelector<HTMLElement>('[data-slot="composer-surface"]')
  expect(surface).toBeTruthy()

  const fade = surface!.querySelector<HTMLElement>('[data-slot="composer-fade"]')
  expect(fade).toBeTruthy()

  return { view, surface: surface!, fade: fade! }
}

describe('clicking the empty composer bar while a run is active stops it (#141444)', () => {
  it('stops the run when the primary control is in its Stop state', async () => {
    const onCancel = vi.fn()
    const { fade } = await mount(true, onCancel)

    fireEvent.click(fade)
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('keeps the click inert while the turn is parked on the user', async () => {
    // Same exemption as Esc-to-stop: a clarify/approval prompt owns its own
    // dismissal, and stopping the run out from under it would discard it.
    setApprovalRequest({ command: 'x', description: 'd', sessionId: 'session-a' })

    const onCancel = vi.fn()
    const { fade } = await mount(true, onCancel)

    fireEvent.click(fade)
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('never turns a click into a stop while a draft is staged', async () => {
    const onCancel = vi.fn()
    const { view, fade } = await mount(true, onCancel)

    const editor = view.container.querySelector<HTMLElement>(`[data-slot="${RICH_INPUT_SLOT}"]`)
    expect(editor).toBeTruthy()

    // Type, then flush via compositionend — the synchronous flush path — and
    // prove the payload landed: the primary control must have left its Stop
    // state before the click does.
    editor!.textContent = 'half-typed follow-up'
    await act(async () => {
      fireEvent.compositionEnd(editor!)
    })
    expect(view.container.querySelector(`button[aria-label="${en.composer.stop}"]`)).toBeNull()

    fireEvent.click(fade)
    expect(onCancel).not.toHaveBeenCalled()

    // The AUI composer state outlives the provider (a module-level store), so
    // unstage what this test staged or the next mount inherits the draft.
    editor!.textContent = ''
    await act(async () => {
      fireEvent.compositionEnd(editor!)
    })
  })

  it('leaves clicks on the contentEditable to place the caret, not stop', async () => {
    const onCancel = vi.fn()
    const { view } = await mount(true, onCancel)

    const editor = view.container.querySelector<HTMLElement>(`[data-slot="${RICH_INPUT_SLOT}"]`)
    expect(editor).toBeTruthy()

    fireEvent.click(editor!)
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('leaves clicks on the Stop button on the submit path the button already owns', async () => {
    const onCancel = vi.fn()
    const { view } = await mount(true, onCancel)

    // The primary control is the small square Stop button while busy + empty.
    const stop = view.getByRole('button', { name: en.composer.stop })
    fireEvent.click(stop)
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('does nothing when no run is active', async () => {
    const onCancel = vi.fn()
    const { fade } = await mount(false, onCancel)

    fireEvent.click(fade)
    expect(onCancel).not.toHaveBeenCalled()
  })
})
