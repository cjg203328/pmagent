import { useEffect, useState } from 'react'
import { Activity, RefreshCw } from 'lucide-react'

import { fetchReadiness } from '@/lib/api'
import type { ReadinessPayload } from '@/lib/types'
import { useAppStore } from '@/store/app-store'
import { cn } from '@/lib/utils'

export function ObservabilityPage() {
  const connection = useAppStore((state) => state.connection)
  const conversations = useAppStore((state) => state.conversations)
  const messages = useAppStore((state) => state.messages)
  const capabilities = useAppStore((state) => state.capabilities)
  const [readiness, setReadiness] = useState<ReadinessPayload | null>(null)
  const [loading, setLoading] = useState(false)

  const refresh = async () => {
    setLoading(true)
    try {
      setReadiness(await fetchReadiness())
    } catch {
      setReadiness(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const checks = readiness?.checks ?? {}

  return (
    <div className="page-shell h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-8">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-accent">
              Runtime overview
            </div>
            <h1 className="mt-2 text-[28px] font-semibold tracking-[-0.04em] text-ink">可观测</h1>
            <p className="mt-1 text-[14px] text-muted">
              网关健康状态与本地会话统计
            </p>
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            className={cn(
              'flex items-center gap-1.5 rounded-xl border border-line bg-paper px-3.5 py-2',
              'text-[14px] text-ink-secondary transition-colors hover:border-accent/40 hover:text-accent',
            )}
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>

        {/* 网关状态 */}
        <div className="mt-8 card p-5">
          <div className="flex items-center gap-2">
            <Activity size={16} className="text-accent" />
            <span className="text-[15px] font-medium text-ink">网关就绪状态</span>
            <span
              className={cn(
                'ml-auto state-dot',
                connection === 'online'
                  ? 'online'
                  : connection === 'offline'
                    ? 'offline'
                    : 'checking animate-pulse-soft',
              )}
            />
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-[14px] sm:grid-cols-3">
            <Stat label="服务" value={readiness?.service ?? '—'} />
            <Stat label="版本" value={readiness?.version ?? '—'} />
            <Stat label="状态" value={readiness?.status ?? '—'} />
          </dl>
        </div>

        {/* 存储检查 */}
        <div className="mt-10 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
          Storage health
        </div>
        <h2 className="mt-1 text-[18px] font-semibold text-ink">存储检查</h2>
        {Object.keys(checks).length === 0 ? (
          <p className="mt-2 text-[14px] text-muted">暂无检查项（网关离线时不返回）。</p>
        ) : (
          <ul className="mt-3 divide-y divide-line-light overflow-hidden rounded-lg border border-line-light bg-paper">
            {Object.entries(checks).map(([name, value]) => {
              const ok = value?.status === 'ok' || value?.status === 'ready'
              return (
                <li key={name} className="flex items-center gap-3 px-4 py-2.5">
                  <span className={cn('state-dot', ok ? 'online' : 'checking')} />
                  <span className="font-mono text-[14px] text-ink">{name}</span>
                  <span className="ml-auto text-[13px] text-muted">
                    {value?.status ?? 'unknown'}
                  </span>
                </li>
              )
            })}
          </ul>
        )}

        {/* 本地统计 */}
        <div className="mt-10 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
          Workspace pulse
        </div>
        <h2 className="mt-1 text-[18px] font-semibold text-ink">本地统计</h2>
        <dl className="mt-3 grid gap-3.5 sm:grid-cols-3">
          <MetricCard label="会话数" value={conversations.length} />
          <MetricCard label="当前消息" value={messages.length} />
          <MetricCard label="注册能力" value={capabilities.length} />
        </dl>
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[13px] text-muted">{label}</dt>
      <dd className="mt-0.5 truncate font-mono text-ink">{value}</dd>
    </div>
  )
}

function MetricCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="card card-hover px-5 py-5">
      <div className="metric-value text-3xl font-semibold text-ink">{value}</div>
      <div className="mt-1 text-[13px] text-muted">{label}</div>
    </div>
  )
}
