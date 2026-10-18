import { host } from '@hermes/plugin-sdk'

import type { GroupMember } from './types'

export interface CanonicalGroupRoute {
  connectionId: string
  profile: string
}

export interface CanonicalGroupBinding extends CanonicalGroupRoute {
  roomId: string
  roomName?: string
}

export interface CanonicalRoomMember {
  member_id: string
  profile: string
  handle: string
  display_name?: string
  target?: Record<string, unknown>
}

export interface CanonicalRoom {
  room_id: string
  name: string
  members: CanonicalRoomMember[]
  disbanded_at?: number | null
}

export interface CanonicalPendingAction {
  kind: string
  member_id: string
  task_id: string
  execution_generation: number
  request_id?: string
}

function requireRoute(route: CanonicalGroupRoute): void {
  if (!route.connectionId?.trim() || !route.profile?.trim()) {
    throw new Error('Canonical groups require an explicit connection and profile')
  }
}

/** True when the member cannot sit in a room whose authority connection is
 *  `connectionId`: it routes to a different connection, or is a remote row
 *  with no connection of its own. Callers use this to route a cross-connection
 *  roster away from the canonical path before submission instead of failing
 *  there (#141178). */
export function groupMemberOutsideAuthority(
  member: Pick<GroupMember, 'connectionId' | 'remoteSource' | 'route'>,
  connectionId: string
): boolean {
  const memberConnectionId = member.route?.connectionId ?? member.connectionId

  return (memberConnectionId !== undefined && memberConnectionId !== connectionId) ||
    (member.connectionId !== undefined && member.connectionId !== connectionId) ||
    (memberConnectionId === undefined && member.remoteSource === true)
}

export function captureCanonicalGroupRoute(): CanonicalGroupRoute {
  const connectionId = host.state.connectionId.get()

  if (connectionId === null) {throw new Error('Canonical groups require an explicit connection')}
  const route = { connectionId, profile: host.state.profile.get() }
  requireRoute(route)

  return route
}

export async function canonicalGroupRequest<T>(
  route: CanonicalGroupRoute,
  method: string,
  params: Record<string, unknown> = {}
): Promise<T> {
  requireRoute(route)

  if (params.profile !== undefined && params.profile !== route.profile) {
    throw new Error('Canonical group profile does not match its authority')
  }

  // The descriptor overload never falls back to the foreground gateway.
  return host.requestProfile<T>({
    connectionId: route.connectionId,
    profile: route.profile,
    targetProfile: route.profile,
    mode: route.connectionId === 'local' ? 'local' : 'remote'
  }, method, { ...params, profile: route.profile })
}

export async function discoverCanonicalGroups(
  route: CanonicalGroupRoute
): Promise<{ driver: boolean; rooms: CanonicalRoom[] }> {
  const capabilities = await canonicalGroupRequest<{ driver: boolean }>(route, 'groups.capabilities')
  const rooms: CanonicalRoom[] = []
  let offset = 0

  for (;;) {
    const page = await canonicalGroupRequest<{ rooms: CanonicalRoom[]; next_offset: number | null }>(
      route, 'groups.list', { limit: 100, offset }
    )

    rooms.push(...page.rooms)

    if (page.next_offset === null) {break}

    if (!Number.isSafeInteger(page.next_offset) || page.next_offset <= offset) {
      throw new Error('Invalid canonical group pagination cursor')
    }

    offset = page.next_offset
  }

  return { driver: capabilities.driver === true, rooms }
}

export async function createCanonicalGroup(
  route: CanonicalGroupRoute,
  name: string,
  members: GroupMember[]
): Promise<{ binding: CanonicalGroupBinding; room: CanonicalRoom }> {
  requireRoute(route)

  if (!name.trim() || members.length < 2 || members.length > 6) {
    throw new Error('A canonical group needs a name and two to six members')
  }

  const profiles = new Set<string>()
  const handles = new Set(['all', 'everyone'])

  const roster = members.map(member => {
    if (groupMemberOutsideAuthority(member, route.connectionId)) {
      throw new Error('Group members must belong to the same authority connection')
    }

    const profile = member.route?.targetProfile ?? member.targetProfile ?? member.name
    const handle = member.handle ?? profile

    if (!profile.trim() || !handle.trim() || profiles.has(profile.toLowerCase()) || handles.has(handle.toLowerCase())) {
      throw new Error('Group members need unique profiles and non-reserved handles')
    }

    profiles.add(profile.toLowerCase())
    handles.add(handle.toLowerCase())

    return {
      member_id: profile, profile, handle,
      target: { kind: 'local', profile },
      ...(member.display_name ? { display_name: member.display_name } : {})
    }
  })

  const { room } = await canonicalGroupRequest<{ room: CanonicalRoom }>(route, 'groups.create', {
    room_id: crypto.randomUUID(), name, members: roster
  })

  return { binding: { ...route, roomId: room.room_id }, room }
}

export async function actCanonicalGroup(
  binding: CanonicalGroupBinding,
  action: CanonicalPendingAction,
  choice?: 'once' | 'deny'
): Promise<Record<string, unknown>> {
  const methods: Record<string, string> = { retry: 'groups.retry', discard: 'groups.discard', approval: 'groups.approve' }
  const method = Object.hasOwn(methods, action.kind) ? methods[action.kind] : undefined

  if (!method || !binding.roomId || !action.member_id || !action.task_id ||
    !Number.isSafeInteger(action.execution_generation) || action.execution_generation < 1) {
    throw new Error('Invalid canonical group pending action')
  }

  const params: Record<string, unknown> = {
    room_id: binding.roomId,
    member_id: action.member_id,
    task_id: action.task_id,
    execution_generation: action.execution_generation
  }

  if (action.kind === 'approval') {
    if (!action.request_id || (choice !== 'once' && choice !== 'deny')) {
      throw new Error('Approval requires its exact request ID and an explicit choice')
    }

    params.request_id = action.request_id
    params.choice = choice
  }

  return canonicalGroupRequest(binding, method, params)
}
