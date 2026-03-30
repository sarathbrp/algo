import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type BotState } from '@/lib/api'
import { useViewingUserId } from '@/store/sessionStore'
import { useAuthStore } from '@/store/authStore'
import { useState } from 'react'

function describeState(status: BotState): string {
  if (status === 'paused') return 'Bot keeps syncing and can manage open positions, but will not open new trades.'
  if (status === 'stopped') return 'Bot still syncs account state, but will not place automated orders for this account.'
  return 'Bot can sync, manage open positions, and open new trades.'
}

function formatAgeLabel(iso: string | null): string {
  if (!iso) return 'No heartbeat yet'
  const hasTz = iso.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(iso)
  const ts = new Date(hasTz ? iso : iso + 'Z').getTime()
  const diffMs = Math.max(0, Date.now() - ts)
  const totalSec = Math.floor(diffMs / 1000)
  if (totalSec < 60) return `${totalSec}s ago`
  const mins = Math.floor(totalSec / 60)
  const secs = totalSec % 60
  return `${mins}m ${String(secs).padStart(2, '0')}s ago`
}

export function useBotStatus() {
  const userId = useViewingUserId()
  const authPaper = useAuthStore((s) => s.paper)
  const queryClient = useQueryClient()
  const [mutationError, setMutationError] = useState<string | null>(null)

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['accountSettings', userId],
    queryFn: () => api.accountSettings(userId!),
    enabled: !!userId,
    staleTime: 5_000,
    refetchInterval: 10_000,
  })

  const mutation = useMutation({
    mutationFn: (bot_state: BotState) => api.updateBotControl(userId!, { bot_state }),
    onMutate: () => {
      setMutationError(null)
    },
    onSuccess: (next) => {
      queryClient.setQueryData(['accountSettings', userId], next)
    },
    onError: (error: unknown) => {
      const message = typeof error === 'object' && error !== null && 'response' in error
        ? String((error as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Unable to update bot controls.')
        : 'Unable to update bot controls.'
      setMutationError(message)
    },
  })

  const mode = data?.paper ? 'paper' as const : 'live' as const
  const isConfigured = Boolean(data)
  const status = data?.bot_state ?? 'stopped'
  const workerStatus = data?.worker_status ?? 'unknown'
  const heartbeatLabel = formatAgeLabel(data?.worker_last_heartbeat ?? null)
  const hasHeartbeat = Boolean(data?.worker_last_heartbeat)
  const workerSummary = !data
    ? 'Finish account setup to control automation.'
    : workerStatus === 'unknown' || !hasHeartbeat
      ? 'Waiting for the worker to pick up this account.'
      : status === 'running'
        ? 'Automation is live and ready to react.'
        : status === 'paused'
          ? 'Automation is still monitoring, but new entries are paused.'
          : 'Automation is disabled for new orders on this account.'

  return {
    userId,
    status,
    mode: data ? mode : authPaper ? 'paper' as const : 'live' as const,
    tradingEnabled: data?.trading_enabled ?? false,
    strategySlug: data?.strategy_slug ?? 'trend_following',
    riskProfile: data?.risk_profile ?? 'balanced',
    maxPositions: data?.max_positions ?? null,
    description: data?.bot_state_description ?? describeState(status),
    workerStatus,
    workerCurrentUserId: data?.worker_current_user_id ?? null,
    heartbeatLabel,
    reconciledAt: data?.worker_last_reconciled_at,
    hasHeartbeat,
    workerSummary,
    isConfigured,
    mutationError,
    isLoading,
    isRefreshing: isFetching,
    isMutating: mutation.isPending,
    setStatus: (next: BotState) => mutation.mutate(next),
  }
}
