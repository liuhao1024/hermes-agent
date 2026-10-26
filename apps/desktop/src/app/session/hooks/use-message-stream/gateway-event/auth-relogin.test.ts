import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { notify } from '@/store/notifications'
import { requestRoute } from '@/store/recovery-requests'

import { handleAuthReloginEvent } from './auth-relogin'
import type { GatewayEventContext } from './types'

vi.mock('@/store/notifications', () => ({ notify: vi.fn() }))
vi.mock('@/store/recovery-requests', () => ({ requestRoute: vi.fn() }))

function reloginContext(message?: string): GatewayEventContext {
  const payload = message === undefined ? undefined : { message }

  return {
    deps: {} as GatewayEventContext['deps'],
    event: { payload, type: 'nous_auth.relogin_required' },
    explicitSid: '',
    fromActiveSource: () => true,
    isActiveEvent: false,
    occurredAt: 1_700_000_100,
    payload: payload as GatewayEventContext['payload'],
    scheduleConfigRefresh: vi.fn(),
    sessionId: null
  }
}

describe('handleAuthReloginEvent', () => {
  beforeEach(() => {
    vi.mocked(notify).mockClear()
    vi.mocked(requestRoute).mockClear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('raises a sticky toast with a jump into Settings → Keys', () => {
    expect(handleAuthReloginEvent(reloginContext('No access token found for Nous Portal login.'))).toBe(true)

    expect(vi.mocked(notify)).toHaveBeenCalledTimes(1)
    const toast = vi.mocked(notify).mock.calls[0][0]
    expect(toast.kind).toBe('error')
    expect(toast.title).toBe('Nous Portal disconnected')
    expect(toast.message).toBe('No access token found for Nous Portal login.')
    expect(toast.action?.label).toBe('Re-login')
    toast.action?.onClick()
    expect(vi.mocked(requestRoute)).toHaveBeenCalledWith('/settings?tab=keys')
  })

  it('falls back to default copy when the event carries no message', () => {
    expect(handleAuthReloginEvent(reloginContext())).toBe(true)

    const toast = vi.mocked(notify).mock.calls[0][0]
    expect(toast.message).toContain('Nous Portal login')
    expect(vi.mocked(requestRoute)).not.toHaveBeenCalled()
  })

  it('ignores every other event type', () => {
    const ctx = reloginContext()
    ctx.event = { ...ctx.event, type: 'setup.ready' }

    expect(handleAuthReloginEvent(ctx)).toBe(false)
    expect(vi.mocked(notify)).not.toHaveBeenCalled()
  })
})
