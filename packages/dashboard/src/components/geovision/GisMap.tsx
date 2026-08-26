// packages/dashboard/src/components/geovision/GisMap.tsx
'use client'

// The primary visual component: a district choropleth over the selected metric.
//
// Loaded only via next/dynamic with ssr:false (see GisMapPanel) because Leaflet
// touches `window` at import time, and this app is a static export.
import 'leaflet/dist/leaflet.css'

import type { Feature, FeatureCollection, Geometry } from 'geojson'
import type { Layer, PathOptions, LeafletMouseEvent } from 'leaflet'
import { useEffect, useMemo, useRef } from 'react'
import { GeoJSON, MapContainer, TileLayer, useMap } from 'react-leaflet'

import type { RegionsResponse, RegionSummary } from '@/services/api/geovisionApi'
import { useGeovisionFilters, type MapLayer } from '@/store/geovisionFilters'
import {
  buildContinuousScale,
  categoricalColour,
  conditionColour,
  continuousColour,
  getLayer,
  NO_DATA_COLOUR,
  paletteFor,
  type ConditionLevel,
  type ContinuousScale,
} from './mapScales'
import { formatDate, formatValue, NO_VALUE } from './primitives'

// Roughly the bounding box of Pakistan. Used as the initial view so the map
// opens on the area of interest rather than mid-Atlantic.
const PAKISTAN_CENTER: [number, number] = [30.4, 69.3]
const PAKISTAN_ZOOM = 5

// The geometry types Leaflet's geometryToLayer actually handles. Anything else
// makes it throw "Invalid GeoJSON object", which is an *unhandled* error: it
// escapes render and takes the whole dashboard down, not just the map.
//
// The backend normalises its output (see region_geometry.sanitise_features),
// so this should never filter anything. It exists because the cost of being
// wrong is a blank page, and the cost of the guard is one array pass — a
// second source of boundaries, a stale cached payload, or a future Earth
// Engine quirk should degrade to "one district missing", not "site down".
const RENDERABLE_GEOMETRY_TYPES = new Set([
  'Point',
  'MultiPoint',
  'LineString',
  'MultiLineString',
  'Polygon',
  'MultiPolygon',
])

function isRenderable(feature: Feature): boolean {
  const geometry = feature?.geometry as { type?: string } | null
  return Boolean(geometry?.type && RENDERABLE_GEOMETRY_TYPES.has(geometry.type))
}

/** Value shown for a region under the active layer. */
function valueFor(region: RegionSummary | undefined, layer: MapLayer): number | null {
  if (!region) return null
  const def = getLayer(layer)
  // Rainfall on the map is the accumulated total over the selected window, not
  // the most recent daily depth — a single day's mm tells you almost nothing
  // about a region's state.
  if (def.metric === 'rainfall_mm' && region.rainfall_window_total) {
    return region.rainfall_window_total.value
  }
  return region.metrics[def.metric]?.value ?? null
}

/** Pans/zooms to a region when one is selected elsewhere (search, table). */
function SelectionFocus({
  geojson,
  selectedRegionId,
}: {
  geojson: FeatureCollection | undefined
  selectedRegionId: string | null
}) {
  const map = useMap()
  const lastFocused = useRef<string | null>(null)

  useEffect(() => {
    if (!selectedRegionId || !geojson) return
    // Only fly when the selection actually changes; re-fitting on every render
    // would fight the user's own panning.
    if (lastFocused.current === selectedRegionId) return

    const feature = geojson.features.find(
      (f) => (f.properties as { region_id?: string })?.region_id === selectedRegionId
    )
    if (!feature) return

    void import('leaflet').then((L) => {
      const bounds = L.geoJSON(feature as Feature).getBounds()
      if (bounds.isValid()) {
        map.fitBounds(bounds, { padding: [40, 40], maxZoom: 9 })
        lastFocused.current = selectedRegionId
      }
    })
  }, [selectedRegionId, geojson, map])

  return null
}

/** The condition level recorded for a region under the active layer. */
function conditionFor(
  region: RegionSummary | undefined,
  layer: MapLayer
): ConditionLevel | undefined {
  return region?.conditions?.[layer]?.level as ConditionLevel | undefined
}

export default function GisMap({
  geojson,
  regions,
  classification,
  colourMode,
}: {
  geojson: FeatureCollection
  regions: RegionSummary[]
  classification: RegionsResponse['classification'] | undefined
  colourMode: 'condition' | 'value'
}) {
  const mapLayer = useGeovisionFilters((s) => s.mapLayer)
  const selectedRegionId = useGeovisionFilters((s) => s.selectedRegionId)
  const selectRegion = useGeovisionFilters((s) => s.selectRegion)
  const province = useGeovisionFilters((s) => s.province)
  const district = useGeovisionFilters((s) => s.district)

  const definition = getLayer(mapLayer)

  const byRegion = useMemo(() => {
    const map = new Map<string, RegionSummary>()
    for (const r of regions) map.set(r.region_id, r)
    return map
  }, [regions])

  // The map only paints regions that survive the active geography filter, so
  // the map, tables and KPIs always describe the same selection.
  const visibleIds = useMemo(() => new Set(byRegion.keys()), [byRegion])

  const scale: ContinuousScale | null = useMemo(() => {
    if (definition.kind !== 'continuous') return null
    const values = regions
      .map((r) => valueFor(r, mapLayer))
      .filter((v): v is number => v !== null)
    return buildContinuousScale(values, mapLayer)
  }, [regions, mapLayer, definition.kind])

  const bands =
    mapLayer === 'crop_condition'
      ? classification?.crop_condition
      : classification?.ndvi

  function colourFor(regionId: string): string {
    const region = byRegion.get(regionId)

    if (colourMode === 'condition') {
      // Severity view: green healthy, yellow watch, orange stressed, red
      // critical, grey not assessed. The level is decided by the backend so
      // the map, the tooltip and Satellite Watch cannot disagree.
      return conditionColour(conditionFor(region, mapLayer))
    }

    const value = valueFor(region, mapLayer)
    if (value === null) return NO_DATA_COLOUR
    if (definition.kind === 'categorical') {
      return categoricalColour(value, bands ?? [], paletteFor(mapLayer))
    }
    return continuousColour(value, scale)
  }

  function style(feature?: Feature<Geometry, Record<string, unknown>>): PathOptions {
    const regionId = String(feature?.properties?.region_id ?? '')
    const inSelection = visibleIds.has(regionId)
    const isSelected = regionId === selectedRegionId

    return {
      fillColor: inSelection ? colourFor(regionId) : '#e2e8f0',
      // Regions filtered out stay visible but recede, so the user keeps
      // geographic context instead of seeing shapes vanish.
      fillOpacity: inSelection ? 0.75 : 0.15,
      color: isSelected ? '#0f172a' : '#94a3b8',
      weight: isSelected ? 2.5 : 0.6,
      opacity: inSelection ? 1 : 0.4,
    }
  }

  function onEachFeature(
    feature: Feature<Geometry, Record<string, unknown>>,
    layer: Layer
  ) {
    const props = feature.properties as {
      region_id?: string
      name?: string
      province?: string | null
      district?: string | null
      tehsil?: string | null
    }
    const regionId = String(props.region_id ?? '')
    const region = byRegion.get(regionId)

    const rows: string[] = []
    const push = (label: string, text: string) => {
      rows.push(
        `<div style="display:flex;gap:8px;justify-content:space-between"><span style="opacity:.7">${label}</span><strong>${text}</strong></div>`
      )
    }

    if (region) {
      const ndvi = region.metrics.ndvi
      const evi = region.metrics.evi
      const lst = region.metrics.lst_day_c
      push('NDVI', ndvi ? formatValue(ndvi.value, '', 3) : NO_VALUE)
      push('EVI', evi ? formatValue(evi.value, '', 3) : NO_VALUE)
      push(
        'Rainfall',
        region.rainfall_window_total
          ? formatValue(region.rainfall_window_total.value, 'mm', 1)
          : NO_VALUE
      )
      push('LST (day)', lst ? formatValue(lst.value, '°C', 1) : NO_VALUE)
      push('Crop condition', region.crop_condition ?? NO_VALUE)
      push('Latest obs', formatDate(region.latest_observation))

      // Why this district is the colour it is — stated, not left to be
      // inferred from a swatch.
      const condition = region.conditions?.[mapLayer]
      if (condition) {
        rows.push(
          `<div style="margin-top:6px;padding-top:6px;border-top:1px solid rgba(0,0,0,.12)">
             <div style="display:flex;gap:8px;justify-content:space-between;align-items:center">
               <span style="opacity:.7">${definition.label}</span>
               <strong style="color:${conditionColour(condition.level as ConditionLevel)}">
                 ${condition.level.toUpperCase()}
               </strong>
             </div>
             <div style="opacity:.65;margin-top:2px">${condition.detail}</div>
           </div>`
        )
      }
    } else {
      rows.push(
        '<div style="opacity:.7">No observations stored for this region</div>'
      )
    }

    const heading = [props.name, props.district, props.province]
      .filter(Boolean)
      .filter((v, i, arr) => arr.indexOf(v) === i)
      .join(' · ')

    layer.bindTooltip(
      `<div style="font-size:11px;line-height:1.5;min-width:170px">
         <div style="font-weight:600;margin-bottom:4px">${heading || regionId}</div>
         ${rows.join('')}
       </div>`,
      { sticky: true, direction: 'auto', opacity: 0.97 }
    )

    layer.on({
      click: () => selectRegion(regionId === selectedRegionId ? null : regionId),
      mouseover: (e: LeafletMouseEvent) => {
        const target = e.target as { setStyle?: (o: PathOptions) => void }
        target.setStyle?.({ weight: 2, color: '#0f172a' })
      },
      mouseout: (e: LeafletMouseEvent) => {
        const target = e.target as { setStyle?: (o: PathOptions) => void }
        target.setStyle?.(style(feature))
      },
    })
  }

  // Screened before Leaflet ever sees it — see RENDERABLE_GEOMETRY_TYPES.
  const safeGeojson = useMemo<FeatureCollection>(() => {
    const features = (geojson?.features ?? []).filter(isRenderable)
    if (features.length !== (geojson?.features?.length ?? 0)) {
      // Visible in the console rather than silent: a dropped district is a
      // real data problem worth someone noticing.
      console.warn(
        `GisMap: skipped ${(geojson?.features?.length ?? 0) - features.length} ` +
          'feature(s) with geometry Leaflet cannot render'
      )
    }
    return { type: 'FeatureCollection', features }
  }, [geojson])

  // Remounting the GeoJSON layer is the supported way to re-run style() and
  // re-bind tooltips: react-leaflet does not diff feature styles on prop change.
  const layerKey = `${mapLayer}:${colourMode}:${selectedRegionId ?? ''}:${province ?? ''}:${district ?? ''}:${regions.length}:${safeGeojson.features.length}`

  return (
    <MapContainer
      center={PAKISTAN_CENTER}
      zoom={PAKISTAN_ZOOM}
      scrollWheelZoom
      style={{ height: '100%', width: '100%', background: 'transparent' }}
      className="z-0"
    >
      <TileLayer
        // CARTO Positron: a muted basemap, so the thematic fill carries the
        // information rather than competing with road and label colour.
        url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
        maxZoom={19}
      />
      <GeoJSON
        key={layerKey}
        data={safeGeojson}
        style={style}
        onEachFeature={onEachFeature}
      />
      <SelectionFocus geojson={safeGeojson} selectedRegionId={selectedRegionId} />
    </MapContainer>
  )
}
