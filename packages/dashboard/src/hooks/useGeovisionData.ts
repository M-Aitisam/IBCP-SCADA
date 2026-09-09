// packages/dashboard/src/hooks/useGeovisionData.ts
'use client'

import { useCallback, useEffect, useState } from 'react'
import { apiErrorMessage } from '@/utils/axios'
import geovisionApi, {
  DatasetCoverage,
  MetricEnvelope,
  RainfallTotal,
  RegionObservation,
  VegetationObservation,
} from '@/services/api/geovisionApi'

export interface GeovisionData {
  vegetation: MetricEnvelope<VegetationObservation> | null
  rainfall: MetricEnvelope<RainfallTotal> | null
  temperature: MetricEnvelope<RegionObservation> | null
  coverage: DatasetCoverage[]
}

const EMPTY: GeovisionData = {
  vegetation: null,
  rainfall: null,
  temperature: null,
  coverage: [],
}

export function useGeovisionData(days = 90) {
  const [data, setData] = useState<GeovisionData>(EMPTY)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    async (signal?: { cancelled: boolean }) => {
      setLoading(true)
      setError(null)
      try {
        // One failing endpoint should not blank the whole page, so results are
        // settled individually rather than with Promise.all.
        const [vegetation, rainfall, temperature, coverage] =
          await Promise.allSettled([
            geovisionApi.vegetation(days),
            geovisionApi.rainfall(days),
            geovisionApi.temperature(days),
            geovisionApi.coverage(),
          ])

        if (signal?.cancelled) return

        setData({
          vegetation: vegetation.status === 'fulfilled' ? vegetation.value : null,
          rainfall: rainfall.status === 'fulfilled' ? rainfall.value : null,
          temperature: temperature.status === 'fulfilled' ? temperature.value : null,
          coverage: coverage.status === 'fulfilled' ? coverage.value.datasets : [],
        })

        const failures = [vegetation, rainfall, temperature, coverage].filter(
          (r): r is PromiseRejectedResult => r.status === 'rejected'
        )
        if (failures.length > 0) {
          setError(apiErrorMessage(failures[0].reason, 'Some data could not be loaded'))
        }
      } finally {
        if (!signal?.cancelled) setLoading(false)
      }
    },
    [days]
  )

  useEffect(() => {
    const signal = { cancelled: false }
    void load(signal)
    return () => {
      signal.cancelled = true
    }
  }, [load])

  const hasAnyData =
    (data.vegetation?.count ?? 0) > 0 ||
    (data.rainfall?.count ?? 0) > 0 ||
    (data.temperature?.count ?? 0) > 0

  return { data, loading, error, hasAnyData, refresh: () => load() }
}

export default useGeovisionData
