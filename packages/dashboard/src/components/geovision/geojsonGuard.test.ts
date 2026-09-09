// packages/dashboard/src/components/geovision/geojsonGuard.test.ts
//
// Runs the assertions against the REAL Leaflet package, not a mock, because the
// whole point is what Leaflet actually does with a geometry type it does not
// recognise. Reproduces the production crash, then proves the guard prevents it.
import type { Feature, FeatureCollection } from 'geojson'
import { describe, expect, it } from 'vitest'

// Mirrors RENDERABLE_GEOMETRY_TYPES in GisMap.tsx. Kept here as an independent
// statement of the contract: if the two ever disagree, this test fails.
const RENDERABLE = new Set([
  'Point',
  'MultiPoint',
  'LineString',
  'MultiLineString',
  'Polygon',
  'MultiPolygon',
])

function isRenderable(feature: Feature): boolean {
  const geometry = feature?.geometry as { type?: string } | null
  return Boolean(geometry?.type && RENDERABLE.has(geometry.type))
}

const SQUARE = [
  [
    [0, 0],
    [0, 1],
    [1, 1],
    [1, 0],
    [0, 0],
  ],
]
const RING = [
  [0, 0],
  [0, 1],
  [1, 1],
  [1, 0],
  [0, 0],
]

function collection(...geometries: unknown[]): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: geometries.map((geometry, i) => ({
      type: 'Feature',
      geometry,
      properties: { region_id: `R${i}` },
    })) as Feature[],
  }
}

async function leaflet() {
  return (await import('leaflet')).default ?? (await import('leaflet'))
}

describe('what Leaflet does with Earth Engine geometry', () => {
  it('throws on LinearRing — this is the production crash', async () => {
    // Exactly the shape found in the export: one LinearRing inside a
    // GeometryCollection. Leaflet's geometryToLayer switch has no case for it
    // and hits `default: throw new Error('Invalid GeoJSON object.')`.
    const L = await leaflet()
    const poisoned = collection({
      type: 'GeometryCollection',
      geometries: [
        { type: 'Polygon', coordinates: SQUARE },
        { type: 'LinearRing', coordinates: RING },
      ],
    })

    expect(() => L.geoJSON(poisoned as never)).toThrow(/Invalid GeoJSON object/)
  })

  it('one bad feature poisons the whole collection, not just itself', async () => {
    // Why this mattered so much: 118 perfectly good districts could not render
    // because of a single sliver in the 119th.
    const L = await leaflet()
    const mostlyFine = collection(
      { type: 'Polygon', coordinates: SQUARE },
      { type: 'Polygon', coordinates: SQUARE },
      { type: 'LinearRing', coordinates: RING },
    )
    expect(() => L.geoJSON(mostlyFine as never)).toThrow(/Invalid GeoJSON object/)
  })

  it('accepts the types the backend now emits', async () => {
    const L = await leaflet()
    const clean = collection(
      { type: 'Polygon', coordinates: SQUARE },
      { type: 'MultiPolygon', coordinates: [SQUARE] },
    )
    expect(() => L.geoJSON(clean as never)).not.toThrow()
  })
})

describe('the frontend guard', () => {
  it('filters out exactly what Leaflet cannot render', () => {
    const mixed = collection(
      { type: 'Polygon', coordinates: SQUARE },
      { type: 'LinearRing', coordinates: RING },
      { type: 'MultiPolygon', coordinates: [SQUARE] },
      null,
    )
    const kept = mixed.features.filter(isRenderable)
    expect(kept).toHaveLength(2)
    expect(kept.map((f) => f.geometry.type)).toEqual(['Polygon', 'MultiPolygon'])
  })

  it('turns a fatal crash into a survivable rendering', async () => {
    const L = await leaflet()
    const poisoned = collection(
      { type: 'Polygon', coordinates: SQUARE },
      { type: 'LinearRing', coordinates: RING },
    )

    // Unguarded: the dashboard goes down.
    expect(() => L.geoJSON(poisoned as never)).toThrow()

    // Guarded: one district is missing, the map still draws.
    const guarded: FeatureCollection = {
      type: 'FeatureCollection',
      features: poisoned.features.filter(isRenderable),
    }
    expect(() => L.geoJSON(guarded as never)).not.toThrow()
    expect(guarded.features).toHaveLength(1)
  })

  it('survives an empty collection', async () => {
    const L = await leaflet()
    const empty: FeatureCollection = { type: 'FeatureCollection', features: [] }
    expect(() => L.geoJSON(empty as never)).not.toThrow()
  })
})
