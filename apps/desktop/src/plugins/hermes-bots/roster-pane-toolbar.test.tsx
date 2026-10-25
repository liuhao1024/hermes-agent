/**
 * The Bots header's eye button: the control the hide toast promises.
 *
 * Hiding a bot toasts "use the eye button in the Bots header to see hidden
 * bots", but the header never carried one — the only reveal affordances lived
 * at the bottom of the roster list (#144675). The toolbar now drives the same
 * session-only reveal state the list body reads, so the toast's instruction
 * finally names a control that exists in every locale.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { botsText } from './i18n'
import { renderRosterToolbar } from './roster-pane-toolbar'

const showHidden = botsText().roster.showHidden

function renderToolbar(hiddenExpanded: boolean, setHiddenExpanded: (value: boolean) => void) {
  render(
    renderRosterToolbar({
      activeFilterCount: 0,
      activeSourceRoster: [],
      activityFilter: 'all',
      activityToasts: false,
      b: botsText(),
      gatewayFilter: 'all',
      gatewayOptions: [],
      hiddenExpanded,
      setHiddenExpanded,
      query: '',
      roster: [],
      rowKindFilter: 'all',
      setActivityFilter: vi.fn(),
      setCreateOpen: vi.fn(),
      setGatewayFilter: vi.fn(),
      setGroupCreateOpen: vi.fn(),
      setQuery: vi.fn(),
      setRowKindFilter: vi.fn(),
      setSectionDialog: vi.fn(),
      showRosterFilters: false,
      showRosterSearch: false,
      showRosterTools: false
    })
  )
}

describe('roster toolbar hidden-bots eye toggle', () => {
  afterEach(() => {
    cleanup()
  })

  it('carries the eye button the hide toast promises', () => {
    renderToolbar(false, vi.fn())

    const button = screen.getByRole('button', { name: showHidden })
    expect(button.getAttribute('aria-pressed')).toBe('false')
    expect(button.querySelector('.codicon-eye-closed')).toBeTruthy()
    expect(button.querySelector('.codicon-eye')).toBeNull()
  })

  it('reveals through the setter when clicked while concealed', () => {
    const setHiddenExpanded = vi.fn()
    renderToolbar(false, setHiddenExpanded)

    fireEvent.click(screen.getByRole('button', { name: showHidden }))

    expect(setHiddenExpanded).toHaveBeenCalledTimes(1)
    expect(setHiddenExpanded).toHaveBeenCalledWith(true)
  })

  it('flips the glyph and conceal target while hidden bots are revealed', () => {
    const setHiddenExpanded = vi.fn()
    renderToolbar(true, setHiddenExpanded)

    const button = screen.getByRole('button', { name: showHidden })
    expect(button.getAttribute('aria-pressed')).toBe('true')
    expect(button.querySelector('.codicon-eye')).toBeTruthy()
    expect(button.querySelector('.codicon-eye-closed')).toBeNull()

    fireEvent.click(button)
    expect(setHiddenExpanded).toHaveBeenCalledWith(false)
  })
})
