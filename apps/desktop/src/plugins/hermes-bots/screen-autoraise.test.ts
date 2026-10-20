/**
 * Screen auto-raise contract: a live screen-tool call on an opted-in bot raises
 * its Screen tab once; a manual Close holds through the burst; no opt-in, a
 * foreign profile, a non-screen tool, or a host that cannot serve a screen
 * never raises.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { RosterRow } from './types'

const opened: string[] = []
const prefetched: string[] = []
let meta: Record<string, { screenAutoOpen?: boolean }> = {}
let screenCache: Record<string, { status?: { supported: boolean } | null; unavailable?: boolean }> = {}

vi.mock('@hermes/plugin-sdk', () => ({ host: { onEvent: () => () => undefined } }))
vi.mock('./data', () => ({
  $botMeta: { get: () => meta },
  $lastRoster: { get: () => roster },
  botSelectionKey: (bot: RosterRow) => bot.name
}))
vi.mock('./routing', () => ({
  botRosterMeta: (bot: RosterRow) => meta[bot.name],
  resolveBotConnectionRoute: (bot: RosterRow) => ({
    status: 'resolved',
    route: { connectionId: 'local', mode: 'local', profile: bot.name, targetProfile: bot.name }
  })
}))
vi.mock('./screen-open', () => ({
  openBotScreen: (bot: RosterRow) => {
    opened.push(bot.name)
  },
  screenPaneId: (bot: RosterRow) => `screen:${bot.name}`
}))
vi.mock('./screen-state', () => ({
  $screenState: { get: () => screenCache },
  prefetchBotScreenStatus: (bot: RosterRow) => {
    prefetched.push(bot.name)
  },
  screenStateFor: (all: Record<string, unknown>, bot: RosterRow) => all[bot.name] ?? null
}))

const roster = [
  { name: 'parker', sourceScoped: true },
  { name: 'alfred', sourceScoped: true }
] as RosterRow[]

const toolStart = (profile: string, name: string, connectionId?: string) =>
  ({ type: 'tool.start', profile, connectionId, session_id: 's1', payload: { tool_id: 't', name } }) as never

describe('screen auto-raise', () => {
  beforeEach(async () => {
    opened.length = 0
    prefetched.length = 0
    meta = { parker: { screenAutoOpen: true }, alfred: {} }
    screenCache = {}
    const mod = await import('./screen-autoraise')
    mod.resetScreenAutoRaise()
  })

  afterEach(() => {
    vi.resetModules()
  })

  it('raises once per burst for an opted-in bot, and a Close holds until the burst is over', async () => {
    const { AUTO_RAISE_COOLDOWN_MS, handleScreenToolStart, noteScreenTabClosed, noteScreenTabOpened } =
      await import('./screen-autoraise')

    const t0 = 1_000_000

    expect(handleScreenToolStart(toolStart('parker', 'computer_use'), t0)).toBe(true)
    noteScreenTabOpened(roster[0])
    // The tab is open: further calls in the run never re-front it.
    expect(handleScreenToolStart(toolStart('parker', 'browser_navigate'), t0 + 1_000)).toBe(false)

    // The user closes it mid-run: the very next click of the same run must not reopen it.
    noteScreenTabClosed(roster[0], t0 + 2_000)
    expect(handleScreenToolStart(toolStart('parker', 'computer_use'), t0 + 3_000)).toBe(false)
    expect(handleScreenToolStart(toolStart('parker', 'computer_use'), t0 + AUTO_RAISE_COOLDOWN_MS)).toBe(false)

    // A fresh run after a quiet gap may raise again.
    expect(handleScreenToolStart(toolStart('parker', 'computer_use'), t0 + 2_000 + AUTO_RAISE_COOLDOWN_MS)).toBe(true)
    expect(opened).toEqual(['parker', 'parker'])
  })

  it('never raises without the opt-in, for a non-screen tool, or for a profile no bot owns', async () => {
    const { handleScreenToolStart } = await import('./screen-autoraise')

    expect(handleScreenToolStart(toolStart('alfred', 'computer_use'), 5)).toBe(false)
    expect(handleScreenToolStart(toolStart('parker', 'terminal'), 5)).toBe(false)
    expect(handleScreenToolStart(toolStart('nobody', 'computer_use'), 5)).toBe(false)
    // Same profile name on a different source is a different bot.
    expect(handleScreenToolStart(toolStart('parker', 'computer_use', 'ssh-box'), 5)).toBe(false)
    expect(opened).toEqual([])
  })

  it('never raises for a host the cached display status says cannot serve a screen', async () => {
    const { handleScreenToolStart } = await import('./screen-autoraise')

    // A settled "no screen on this host" gates the wake: the tab could only paint the
    // unsupported empty state (#142221).
    screenCache.parker = { status: { supported: false } }
    expect(handleScreenToolStart(toolStart('parker', 'computer_use'), 5)).toBe(false)

    // An older backend without display.* is the same dead-tab answer.
    screenCache.parker = { unavailable: true }
    expect(handleScreenToolStart(toolStart('parker', 'computer_use'), 6)).toBe(false)

    // A host the cache says CAN serve a screen keeps raising.
    screenCache.parker = { status: { supported: true } }
    expect(handleScreenToolStart(toolStart('parker', 'computer_use'), 7)).toBe(true)
    expect(opened).toEqual(['parker'])
  })

  it('triggers the one-shot status fetch for a bot whose display status is not cached yet', async () => {
    const { handleScreenToolStart } = await import('./screen-autoraise')

    // No cached status: this wake still follows the existing rules, but the fetch it
    // triggers means the next one is decided on real data instead of raising blind.
    expect(handleScreenToolStart(toolStart('parker', 'computer_use'), 5)).toBe(true)
    expect(prefetched).toEqual(['parker'])
    expect(opened).toEqual(['parker'])
  })
})
