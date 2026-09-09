// packages/dashboard/src/components/geovision/mapScales.test.ts
import { describe, expect, it } from 'vitest'

import {
  buildContinuousScale,
  categoricalColour,
  continuousColour,
  getLayer,
  MAP_LAYERS,
  NO_DATA_COLOUR,
  paletteFor,
} from './mapScales'

// The same band structure the backend sends: highest threshold first.
const NDVI_BANDS = [
  { min: 0.6, label: 'Very healthy' },
  { min: 0.45, label: 'Healthy' },
  { min: 0.3, label: 'Moderate' },
  { min: 0.15, label: 'Low' },
  { min: -1.01, label: 'Very low / bare' },
]

describe('layer definitions', () => {
  it('maps every layer to a real metric', () => {
    for (const layer of MAP_LAYERS) {
      expect(getLayer(layer.key).key).toBe(layer.key)
      expect(layer.metric).toBeTruthy()
    }
  })

  it('classifies crop condition from NDVI', () => {
    // Crop condition is derived, not measured: it must read the NDVI metric.
    expect(getLayer('crop_condition').metric).toBe('ndvi')
    expect(getLayer('crop_condition').kind).toBe('categorical')
  })

  it('treats rainfall and temperature as continuous', () => {
    // There is no climate-independent threshold for "high rainfall" or "hot",
    // so these must never be given named bands.
    expect(getLayer('rainfall_mm').kind).toBe('continuous')
    expect(getLayer('lst_day_c').kind).toBe('continuous')
    expect(getLayer('lst_night_c').kind).toBe('continuous')
  })
})

describe('categorical colouring', () => {
  it('gives a missing value the no-data colour, not the lowest band', () => {
    // A region with no observation is unknown, not "very low".
    expect(categoricalColour(null, NDVI_BANDS, paletteFor('ndvi'))).toBe(NO_DATA_COLOUR)
    expect(categoricalColour(undefined, NDVI_BANDS, paletteFor('ndvi'))).toBe(
      NO_DATA_COLOUR
    )
  })

  it('assigns distinct colours across the bands', () => {
    const palette = paletteFor('ndvi')
    const colours = [0.7, 0.5, 0.35, 0.2, 0.0].map((v) =>
      categoricalColour(v, NDVI_BANDS, palette)
    )
    expect(new Set(colours).size).toBe(5)
  })

  it('is monotonic: a healthier value never gets a less healthy colour', () => {
    const palette = paletteFor('ndvi')
    const high = categoricalColour(0.7, NDVI_BANDS, palette)
    const low = categoricalColour(0.05, NDVI_BANDS, palette)
    expect(high).toBe(palette[palette.length - 1])
    expect(low).toBe(palette[0])
  })

  it('places a value exactly on a threshold in the higher band', () => {
    const palette = paletteFor('ndvi')
    expect(categoricalColour(0.45, NDVI_BANDS, palette)).toBe(
      categoricalColour(0.5, NDVI_BANDS, palette)
    )
  })
})

describe('continuous scales', () => {
  it('refuses to build a scale with no variation', () => {
    // A ramp across a zero-width range would paint arbitrary colours onto
    // differences that do not exist.
    expect(buildContinuousScale([5, 5, 5], 'rainfall_mm')).toBeNull()
    expect(buildContinuousScale([42], 'lst_day_c')).toBeNull()
    expect(buildContinuousScale([], 'rainfall_mm')).toBeNull()
  })

  it('stretches across the values actually present', () => {
    const scale = buildContinuousScale([10, 50, 90], 'rainfall_mm')
    expect(scale).not.toBeNull()
    expect(scale!.min).toBe(10)
    expect(scale!.max).toBe(90)
  })

  it('ignores non-finite values', () => {
    const scale = buildContinuousScale([10, NaN, 90, Infinity], 'rainfall_mm')
    expect(scale!.min).toBe(10)
    expect(scale!.max).toBe(90)
  })

  it('keeps the extremes inside the ramp', () => {
    const scale = buildContinuousScale([0, 100], 'rainfall_mm')!
    expect(continuousColour(0, scale)).toBe(scale.ramp[0])
    // The maximum must not fall off the end of the palette.
    expect(continuousColour(100, scale)).toBe(scale.ramp[scale.ramp.length - 1])
  })

  it('gives a missing value the no-data colour', () => {
    const scale = buildContinuousScale([0, 100], 'rainfall_mm')!
    expect(continuousColour(null, scale)).toBe(NO_DATA_COLOUR)
  })
})
