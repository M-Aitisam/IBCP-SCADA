// packages/dashboard/src/components/geovision/conditionColours.test.ts
import { describe, expect, it } from 'vitest'

import {
  CONDITION_COLOURS,
  CONDITION_LABELS,
  CONDITION_ORDER,
  conditionColour,
  NO_DATA_COLOUR,
  type ConditionLevel,
} from './mapScales'

describe('traffic-light palette', () => {
  it('covers every level with a distinct colour', () => {
    const colours = CONDITION_ORDER.map((l) => CONDITION_COLOURS[l])
    expect(new Set(colours).size).toBe(CONDITION_ORDER.length)
  })

  it('runs green -> yellow -> orange -> red for increasing severity', () => {
    // Not a colour-science assertion, just the convention every operations
    // dashboard uses; a viewer should not need the legend to read severity.
    expect(CONDITION_COLOURS.healthy).toBe('#16a34a')
    expect(CONDITION_COLOURS.watch).toBe('#eab308')
    expect(CONDITION_COLOURS.stressed).toBe('#f97316')
    expect(CONDITION_COLOURS.critical).toBe('#dc2626')
  })

  it('paints "unknown" grey, never green', () => {
    // The distinction that matters: grey means "no basis to judge", green
    // means "assessed and fine". Colouring an unassessed district green
    // would assert something we have not established.
    expect(CONDITION_COLOURS.unknown).toBe(NO_DATA_COLOUR)
    expect(CONDITION_COLOURS.unknown).not.toBe(CONDITION_COLOURS.healthy)
  })

  it('falls back to grey for a missing or unrecognised level', () => {
    expect(conditionColour(undefined)).toBe(CONDITION_COLOURS.unknown)
    expect(conditionColour(null)).toBe(CONDITION_COLOURS.unknown)
    expect(conditionColour('nonsense' as ConditionLevel)).toBe(
      CONDITION_COLOURS.unknown
    )
  })

  it('maps each level to a colour and a word', () => {
    // Colour is never the only carrier of meaning.
    for (const level of CONDITION_ORDER) {
      expect(conditionColour(level)).toMatch(/^#[0-9a-f]{6}$/i)
      expect(CONDITION_LABELS[level]).toBeTruthy()
    }
  })
})
