// packages/dashboard/src/components/geovision/mapScales.ts
//
// Colour scales for the thematic map layers.
//
// The governing constraint (brief §38): do not invent scientifically
// unsupported thresholds, and document the classification rules. That splits
// the layers into two honest categories:
//
//   CATEGORICAL — NDVI, EVI and crop condition. These have conventional vigour
//     bands that are widely used and are already applied by the backend, which
//     sends them with the region data. The map renders the thresholds the
//     server actually used, so the legend cannot drift from the classification.
//
//   CONTINUOUS — rainfall and land surface temperature. There is NO
//     climate-independent threshold for "high rainfall" or "hot": 30 mm in a
//     week is a drought in Punjab's monsoon and a deluge in Chagai, and a
//     "normal" LST depends on season and elevation. Inventing named bands here
//     would be exactly the unsupported claim the brief forbids. Instead these
//     use a sequential ramp stretched across the values actually present in the
//     current selection, and the legend shows the real numeric endpoints rather
//     than words like "High".
import type { ClassBand, MetricKey } from '@/services/api/geovisionApi'
import type { MapLayer } from '@/store/geovisionFilters'

export type ScaleKind = 'categorical' | 'continuous'

// ---------------------------------------------------------------------------
// Condition colouring — the traffic light
// ---------------------------------------------------------------------------

export type ConditionLevel =
  | 'healthy'
  | 'watch'
  | 'stressed'
  | 'critical'
  | 'unknown'

/**
 * Green → yellow → orange → red, the convention every operations dashboard
 * uses, so a viewer reads severity without consulting the legend.
 *
 * `unknown` is deliberately grey and NOT green. Grey says "we have no basis to
 * judge this region"; green would say "we checked and it is fine". For
 * temperature and rainfall that distinction is the whole point — a district
 * with no seasonal baseline yet has not been cleared, it has not been assessed.
 */
export const CONDITION_COLOURS: Record<ConditionLevel, string> = {
  healthy: '#16a34a',
  watch: '#eab308',
  stressed: '#f97316',
  critical: '#dc2626',
  unknown: '#cbd5e1',
}

export const CONDITION_ORDER: ConditionLevel[] = [
  'healthy',
  'watch',
  'stressed',
  'critical',
  'unknown',
]

/** Fallback labels; the backend sends its own, which take precedence. */
export const CONDITION_LABELS: Record<ConditionLevel, string> = {
  healthy: 'Healthy / normal',
  watch: 'Watch',
  stressed: 'Stressed / high',
  critical: 'Critical',
  unknown: 'No basis',
}

export function conditionColour(level: ConditionLevel | undefined | null): string {
  return CONDITION_COLOURS[level ?? 'unknown'] ?? CONDITION_COLOURS.unknown
}

export interface LayerDefinition {
  key: MapLayer
  label: string
  /** Which stored metric supplies the value. */
  metric: MetricKey
  kind: ScaleKind
  unit: string
  decimals: number
  description: string
}

export const MAP_LAYERS: LayerDefinition[] = [
  {
    key: 'ndvi',
    label: 'NDVI',
    metric: 'ndvi',
    kind: 'categorical',
    unit: 'index',
    decimals: 3,
    description: 'Vegetation vigour, conventional NDVI bands',
  },
  {
    key: 'evi',
    label: 'EVI',
    metric: 'evi',
    kind: 'categorical',
    unit: 'index',
    decimals: 3,
    description: 'Enhanced vegetation index, same band structure as NDVI',
  },
  {
    key: 'crop_condition',
    label: 'Crop condition',
    metric: 'ndvi',
    kind: 'categorical',
    unit: 'class',
    decimals: 3,
    description:
      'Satellite-derived crop/vegetation condition indicator, classified from NDVI',
  },
  {
    key: 'rainfall_mm',
    label: 'Rainfall',
    metric: 'rainfall_mm',
    kind: 'continuous',
    unit: 'mm',
    decimals: 1,
    description: 'CHIRPS rainfall accumulated over the selected period',
  },
  {
    key: 'lst_day_c',
    label: 'LST (day)',
    metric: 'lst_day_c',
    kind: 'continuous',
    unit: '°C',
    decimals: 1,
    description: 'MODIS daytime land surface temperature',
  },
  {
    key: 'lst_night_c',
    label: 'LST (night)',
    metric: 'lst_night_c',
    kind: 'continuous',
    unit: '°C',
    decimals: 1,
    description: 'MODIS nighttime land surface temperature',
  },
]

export function getLayer(key: MapLayer): LayerDefinition {
  return MAP_LAYERS.find((l) => l.key === key) ?? MAP_LAYERS[0]
}

// ---------------------------------------------------------------------------
// Categorical palettes
// ---------------------------------------------------------------------------

/** Brown -> green, the conventional direction for a vegetation index. */
const VEGETATION_COLOURS = ['#a16207', '#ca8a04', '#facc15', '#65a30d', '#15803d']

/** Green -> red for stress severity. Paired with labels, never colour alone. */
const CROP_COLOURS = ['#15803d', '#facc15', '#ea580c', '#b91c1c']

/**
 * Colour for a value under server-supplied class bands.
 *
 * Bands arrive highest-threshold-first (matching the backend's classify()), so
 * the first band whose minimum the value clears is the match, and its index
 * maps directly onto the palette.
 */
export function categoricalColour(
  value: number | null | undefined,
  bands: ClassBand[],
  palette: string[]
): string {
  if (value === null || value === undefined) return NO_DATA_COLOUR
  for (let i = 0; i < bands.length; i += 1) {
    if (value >= bands[i].min) {
      // Palette runs low -> high; bands run high -> low.
      return palette[Math.max(0, palette.length - 1 - i)] ?? NO_DATA_COLOUR
    }
  }
  return palette[0] ?? NO_DATA_COLOUR
}

export function paletteFor(layer: MapLayer): string[] {
  return layer === 'crop_condition' ? CROP_COLOURS : VEGETATION_COLOURS
}

// ---------------------------------------------------------------------------
// Continuous ramps
// ---------------------------------------------------------------------------

/** Pale -> deep blue. Water/rainfall convention. */
const RAINFALL_RAMP = ['#eff6ff', '#bfdbfe', '#60a5fa', '#2563eb', '#1e3a8a']
/** Cool -> hot. Temperature convention. */
const TEMPERATURE_RAMP = ['#1e40af', '#60a5fa', '#fde68a', '#f97316', '#b91c1c']

/** Neutral grey: "no observation", visually distinct from any data colour.
 *  Same value as CONDITION_COLOURS.unknown — both mean "not assessed". */
export const NO_DATA_COLOUR = CONDITION_COLOURS.unknown
export const NO_DATA_COLOUR_DARK = '#334155'

export function rampFor(layer: MapLayer): string[] {
  return layer === 'rainfall_mm' ? RAINFALL_RAMP : TEMPERATURE_RAMP
}

export interface ContinuousScale {
  min: number
  max: number
  ramp: string[]
  /** Numeric edges between ramp buckets, for the legend. */
  stops: number[]
}

/**
 * Build a ramp stretched over the values actually present.
 *
 * Returns null when there is nothing to scale — one region, or all values
 * identical — because a ramp across a zero-width range would paint arbitrary
 * colours onto meaningless differences.
 */
export function buildContinuousScale(
  values: number[],
  layer: MapLayer
): ContinuousScale | null {
  const finite = values.filter((v) => Number.isFinite(v))
  if (finite.length === 0) return null
  const min = Math.min(...finite)
  const max = Math.max(...finite)
  if (max - min < 1e-9) return null

  const ramp = rampFor(layer)
  const step = (max - min) / ramp.length
  const stops = ramp.map((_, i) => min + step * i).concat(max)
  return { min, max, ramp, stops }
}

export function continuousColour(
  value: number | null | undefined,
  scale: ContinuousScale | null
): string {
  if (value === null || value === undefined) return NO_DATA_COLOUR
  if (!scale) return scale === null ? '#60a5fa' : NO_DATA_COLOUR
  const { min, max, ramp } = scale
  const fraction = (value - min) / (max - min)
  const index = Math.min(ramp.length - 1, Math.max(0, Math.floor(fraction * ramp.length)))
  return ramp[index]
}
