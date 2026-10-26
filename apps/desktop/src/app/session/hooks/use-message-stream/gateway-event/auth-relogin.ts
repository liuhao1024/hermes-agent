import { notify } from '@/store/notifications'
import { requestRoute } from '@/store/recovery-requests'

import type { GatewayEventContext } from './types'

/**
 * nous_auth.relogin_required: the background keepalive hit a terminal auth
 * death (revoked or rotated refresh token) and cleared the stored Nous login.
 * The turn path only surfaces auth failures when a request fails, so an idle
 * install stays silent until the next model switch days later. Raise a sticky
 * toast with a jump into Settings → Keys; the backend latches repeats, so one
 * outage means one toast. A fixed id re-renders the same toast instead of
 * stacking if the notice re-arms after a recovery and a second outage lands.
 */
export function handleAuthReloginEvent({ event, payload }: GatewayEventContext): boolean {
  if (event.type !== 'nous_auth.relogin_required') {
    return false
  }

  notify({
    id: 'nous-auth-relogin-required',
    kind: 'error',
    title: 'Nous Portal disconnected',
    message: payload?.message || 'The saved Nous Portal login was rejected and must be signed in again.',
    action: {
      label: 'Re-login',
      onClick: () => requestRoute('/settings?tab=keys')
    }
  })

  return true
}
