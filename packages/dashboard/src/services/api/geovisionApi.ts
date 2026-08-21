// packages/dashboard/src/services/api/geovisionApi.ts
import apiClient from '@/utils/axios'

/** Every list endpoint reports where its numbers came from. */
export type DataSource = 'timestampdb' | 'no_data' | 'none'

export interface RegionObservation {
  region_id: string
  region_type: string
  province: string | null
  district: string | null
  tehsil: string | null
  value: number
  unit: string
  observation_date: string
  dataset: string
  source_image_id: string
  pixel_count: number | null
}

export interface VegetationObservation extends RegionObservation {
  health_status: string
}

export interface RainfallTotal {
  region_id: string
  province: string | null
  district: string | null
  tehsil: string | null
  total_rainfall_mm: number
  observations: number
  latest_observation: string | null
  unit: string
}

export interface MetricEnvelope<T> {
  status: string
  metric: string
  data_source: DataSource
  count: number
  window_days: number
  data: T[]
}

export interface DatasetCoverage {
  dataset: string
  observations: number
  regions: number
  earliest: string | null
  latest: string | null
}

export interface TimeseriesPoint {
  observation_date: string
  observation_timestamp: string
  value: number | null
  unit: string
  dataset: string
  source_image_id: string
  cloud_percentage: number | null
  processing_status: string
}

export const geovisionApi = {
  vegetation: async (days = 90) => {
    const { data } = await apiClient.get<MetricEnvelope<VegetationObservation>>(
      '/geovision/vegetation',
      { params: { days } }
    )
    return data
  },

  rainfall: async (days = 90) => {
    const { data } = await apiClient.get<MetricEnvelope<RainfallTotal>>(
      '/geovision/rainfall',
      { params: { days } }
    )
    return data
  },

  temperature: async (days = 90) => {
    const { data } = await apiClient.get<MetricEnvelope<RegionObservation>>(
      '/geovision/temperature',
      { params: { days } }
    )
    return data
  },

  coverage: async () => {
    const { data } = await apiClient.get<{
      status: string
      datasets: DatasetCoverage[]
    }>('/geovision/coverage')
    return data
  },

  timeseries: async (regionId: string, metric: string, days = 365) => {
    const { data } = await apiClient.get<{
      status: string
      region_id: string
      metric: string
      data_source: DataSource
      count: number
      series: TimeseriesPoint[]
    }>(`/geovision/timeseries/${encodeURIComponent(regionId)}`, {
      params: { metric, days },
    })
    return data
  },
}

export default geovisionApi
