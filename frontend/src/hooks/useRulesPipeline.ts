import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/lib/api'
import { useViewingUserId } from '@/store/sessionStore'

export interface RuleSignal {
  rule_id: number
  rule_name: string
  rule_type: 'entry' | 'exit'
  fired: boolean
  conditions_met: number
  conditions_total: number
  actions_summary: string
  indicator_snapshot: Record<string, number>
}

export interface SymbolPipeline {
  symbol: string
  entry_rules: RuleSignal[]
  exit_rules: RuleSignal[]
  status: 'ready_to_buy' | 'watching' | 'in_position' | 'ready_to_sell' | 'no_signal'
  status_detail: string
}

export interface PipelineSummary {
  total_active_rules: number
  symbols_monitored: number
  pipeline: SymbolPipeline[]
  updated_at: string
}

export function useRulesPipeline() {
  const userId = useViewingUserId()

  const { data, isLoading } = useQuery({
    queryKey: ['rules-pipeline', userId],
    queryFn: () => apiClient.get<PipelineSummary>(`/api/users/${userId}/rules/pipeline`).then(r => r.data),
    enabled: !!userId,
    staleTime: 20_000,
    refetchInterval: 30_000,
  })

  return {
    pipeline: data?.pipeline ?? [],
    totalRules: data?.total_active_rules ?? 0,
    symbolsMonitored: data?.symbols_monitored ?? 0,
    updatedAt: data?.updated_at ?? null,
    isLoading,
  }
}
