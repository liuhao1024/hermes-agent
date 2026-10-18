import type { GatewayClient } from '../gatewayClient.js'
import type { SessionActiveItem, SessionActiveListResponse } from '../gatewayTypes.js'

/** The live-session roster the status bar's title and count read: on the shared owner its own
 * `session.list` (live scope); the sidecar's `session.active_list` never holds those sessions. */
export const requestLiveSessions = (gw: Pick<GatewayClient, 'isCanonical' | 'request'>, currentSid: null | string) =>
  gw.isCanonical
    ? gw.request<SessionActiveListResponse>('session.list', { limit: 200 })
    : gw.request<SessionActiveListResponse>('session.active_list', { current_session_id: currentSid })

/** The status bar's live-session count. Canonical rows come from `session.list` — a stored
 * listing, not the live roster the sidecar's `session.active_list` is — so only rows the
 * gateway stamped `live` count; the listing length read as "N sessions" on stores larger than
 * the limit (#140959). */
export const countLiveSessions = (sessions: SessionActiveItem[], canonical: boolean) =>
  canonical ? sessions.filter(s => s.live).length : sessions.length
