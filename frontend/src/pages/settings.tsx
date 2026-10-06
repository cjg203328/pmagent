import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Database,
  FolderOpen,
  Loader2,
  RotateCcw,
  Save,
  ShieldCheck,
} from 'lucide-react'

import { ACTOR_ID, TENANT_ID, WORKSPACE_ID } from '@/lib/api'
import { useAppStore } from '@/store/app-store'
import type { ConfigFieldPayload } from '@/lib/types'
import { cn } from '@/lib/utils'

export function SettingsPage() {
  const capabilities = useAppStore((state) => state.capabilities)
  const connection = useAppStore((state) => state.connection)
  const config = useAppStore((state) => state.config)
  const configDraft = useAppStore((state) => state.configDraft)
  const configLoading = useAppStore((state) => state.configLoading)
  const configSaving = useAppStore((state) => state.configSaving)
  const configResult = useAppStore((state) => state.configResult)
  const configError = useAppStore((state) => state.configError)
  const loadConfig = useAppStore((state) => state.loadConfig)
  const setConfigField = useAppStore((state) => state.setConfigField)
  const discardConfigDraft = useAppStore((state) => state.discardConfigDraft)
  const saveConfig = useAppStore((state) => state.saveConfig)

  useEffect(() => {
    void loadConfig()
  }, [loadConfig])

  const dirtyCount = Object.keys(configDraft).length

  return (
    <div className="page-shell h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-8">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-accent">
              Workspace configuration
            </div>
            <h1 className="mt-2 text-[28px] font-semibold tracking-[-0.04em] text-ink">设置</h1>
          </div>
          <span className="hidden rounded-full bg-accent-soft px-3 py-1 text-[12px] font-medium text-accent sm:inline-flex">
            {connection === 'online' ? '配置已同步' : '等待网关'}
          </span>
        </div>
        <p className="mt-1 text-[14px] text-muted">
          模型与密钥配置写入项目根目录的{' '}
          <code className="rounded bg-surface px-1.5 py-0.5 font-mono text-[13px]">.env</code>{' '}
          文件。保存后网关会尝试热重载，无需重启即可生效。
        </p>

        <div className="mt-8 grid gap-4 sm:grid-cols-2">
          <SettingsCard
            icon={<Bot size={16} />}
            title="当前模型"
            body="由下方配置决定。未配置 API Key 时，界面以离线模式运行，报价、任务、进度与文档解析仍可用。"
            footer={
              config?.provider
                ? `${config.provider} · ${config.model || '未指定模型'}`
                : '未配置 Provider（离线模式）'
            }
            tone={config?.provider ? 'ok' : 'warn'}
          />
          <SettingsCard
            icon={<Database size={16} />}
            title="数据目录"
            body="会话与消息保存在项目 data/ 目录下的 SQLite 数据库，所有查询按工作区隔离。"
            footer={`工作区 ${WORKSPACE_ID}`}
          />
          <SettingsCard
            icon={<ShieldCheck size={16} />}
            title="身份与权限"
            body="写入型操作需要宿主审批。本地请求以 human 身份提交，审批记录可在可观测页查看。"
            footer={`${ACTOR_ID} · 租户 ${TENANT_ID}`}
          />
          <SettingsCard
            icon={<FolderOpen size={16} />}
            title="远程工具"
            body="MCP 与外部服务均为可选依赖；不可用时自动降级，不影响核心功能。"
            footer="可选"
          />
        </div>

        {/* 模型与密钥配置 */}
        <div className="mt-10 flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
              Gateway configuration
            </div>
            <h2 className="mt-1 text-[18px] font-semibold text-ink">模型与密钥</h2>
          </div>
          <div className="flex items-center gap-2">
            {dirtyCount > 0 ? (
              <span className="text-[12px] text-muted">{dirtyCount} 项未保存</span>
            ) : null}
            <button
              type="button"
              onClick={() => discardConfigDraft()}
              disabled={dirtyCount === 0 || configSaving}
              className={cn(
                'flex items-center gap-1.5 rounded-lg border border-line px-3 py-2 text-[13px] font-medium transition-colors',
                dirtyCount === 0 || configSaving
                  ? 'cursor-not-allowed text-muted opacity-50'
                  : 'text-ink-secondary hover:bg-surface',
              )}
            >
              <RotateCcw size={14} strokeWidth={2.2} />
              放弃修改
            </button>
            <button
              type="button"
              onClick={() => void saveConfig()}
              disabled={dirtyCount === 0 || configSaving}
              className={cn(
                'flex items-center gap-1.5 rounded-lg px-3 py-2 text-[13px] font-semibold text-white transition-colors',
                dirtyCount === 0 || configSaving
                  ? 'cursor-not-allowed bg-accent opacity-50'
                  : 'bg-accent hover:bg-accent-hover',
              )}
            >
              {configSaving ? (
                <Loader2 size={14} strokeWidth={2.4} className="animate-spin" />
              ) : (
                <Save size={14} strokeWidth={2.4} />
              )}
              保存配置
            </button>
          </div>
        </div>

        {configResult ? (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-line-light bg-success-soft px-4 py-3 text-[13px]">
            <CheckCircle2 size={15} strokeWidth={2.4} className="mt-0.5 flex-none text-success" />
            <div className="min-w-0 text-ink">
              <div className="font-medium">
                已保存 {configResult.applied.length} 项配置
              </div>
              <div className="mt-0.5 text-muted">
                {configResult.restart_required
                  ? '部分运行中组件需要重启网关后才会完全生效。'
                  : '运行中组件已热重载，新配置立即生效。'}
              </div>
            </div>
          </div>
        ) : null}

        {configError ? (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-line-light bg-danger-soft px-4 py-3 text-[13px]">
            <AlertTriangle size={15} strokeWidth={2.4} className="mt-0.5 flex-none text-danger" />
            <div className="min-w-0">
              <div className="font-medium text-ink">配置未保存</div>
              <div className="mt-0.5 text-muted">{configError}</div>
            </div>
          </div>
        ) : null}

        {configLoading && !config ? (
          <div className="mt-4 flex items-center gap-2 text-[13px] text-muted">
            <Loader2 size={14} strokeWidth={2.4} className="animate-spin" />
            正在读取网关配置…
          </div>
        ) : null}

        {config ? (
          <>
            <p className="mt-3 text-[12px] text-muted">
              配置文件：
              <code className="ml-1 rounded bg-surface px-1.5 py-0.5 font-mono text-[12px]">
                {config.env_path}
              </code>
            </p>
            <div className="mt-4 space-y-4">
              {config.groups.map((group) => (
                <ConfigGroupCard
                  key={group.id}
                  title={group.title}
                  description={group.description}
                  fields={group.fields}
                  draft={configDraft}
                  onChange={setConfigField}
                />
              ))}
            </div>
          </>
        ) : null}

        {/* 能力注册表 */}
        <div className="mt-10 flex items-end justify-between gap-3">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
              Capability registry
            </div>
            <h2 className="mt-1 text-[18px] font-semibold text-ink">
              已注册能力（{capabilities.length}）
            </h2>
          </div>
          <span className="text-[12px] text-muted">由本地网关提供</span>
        </div>
        {capabilities.length === 0 ? (
          <p className="mt-2 text-[14px] text-muted">
            能力列表需要网关在线后加载。请确认 API 服务已启动。
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-line-light overflow-hidden rounded-lg border border-line-light bg-paper">
            {capabilities.map((capability) => (
              <li key={capability.name} className="flex items-start gap-3 px-4 py-2.5">
                <span className="mt-1.5 state-dot online" />
                <div className="min-w-0">
                  <div className="font-mono text-[14px] text-ink">
                    {capability.name}
                  </div>
                  {capability.description ? (
                    <div className="mt-0.5 text-[13px] text-muted">
                      {String(capability.description)}
                    </div>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function ConfigGroupCard({
  title,
  description,
  fields,
  draft,
  onChange,
}: {
  title: string
  description: string
  fields: ConfigFieldPayload[]
  draft: Record<string, string>
  onChange: (key: string, value: string) => void
}) {
  const [open, setOpen] = useState(true)
  const dirty = useMemo(
    () => fields.filter((field) => field.key in draft).length,
    [fields, draft],
  )

  return (
    <section className="card overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-start justify-between gap-3 px-5 py-4 text-left transition-colors hover:bg-surface/60"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[15px] font-medium text-ink">{title}</span>
            {dirty > 0 ? (
              <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent">
                {dirty} 项已改
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-[13px] leading-relaxed text-muted">{description}</p>
        </div>
        <span className="mt-1 flex-none text-[12px] text-muted">{open ? '收起' : '展开'}</span>
      </button>

      {open ? (
        <div className="divide-y divide-line-light border-t border-line-light">
          {fields.map((field) => (
            <ConfigFieldRow
              key={field.key}
              field={field}
              draftValue={field.key in draft ? draft[field.key] : undefined}
              onChange={onChange}
            />
          ))}
        </div>
      ) : null}
    </section>
  )
}

function ConfigFieldRow({
  field,
  draftValue,
  onChange,
}: {
  field: ConfigFieldPayload
  draftValue: string | undefined
  onChange: (key: string, value: string) => void
}) {
  const inputId = `config-${field.key}`
  const edited = draftValue !== undefined

  // A secret is never sent back by the gateway, so an untouched secret field
  // stays empty and is excluded from the save payload entirely.
  const current = edited ? draftValue : field.kind === 'secret' ? '' : field.value
  const placeholder = field.kind === 'secret'
    ? field.configured
      ? `已配置 ${field.preview ?? ''}（留空则不修改）`
      : '未配置'
    : field.placeholder

  return (
    <div className="flex flex-col gap-2 px-5 py-3.5 sm:flex-row sm:items-center sm:gap-4">
      <div className="sm:w-56 sm:flex-none">
        <label
          htmlFor={inputId}
          className="flex items-center gap-1.5 text-[13px] font-medium text-ink"
        >
          {field.label}
          {edited ? <span className="state-dot warning" aria-hidden="true" /> : null}
        </label>
        <div className="mt-0.5 truncate font-mono text-[11px] text-muted" title={field.key}>
          {field.key}
        </div>
        {field.help ? (
          <div className="mt-1 text-[11.5px] leading-snug text-muted">{field.help}</div>
        ) : null}
      </div>

      <div className="min-w-0 flex-1">
        {field.kind === 'bool' ? (
          <label className="inline-flex cursor-pointer items-center gap-2.5">
            <input
              id={inputId}
              type="checkbox"
              checked={current === 'true'}
              onChange={(event) => onChange(field.key, event.target.checked ? 'true' : 'false')}
              className="peer sr-only"
            />
            <span
              aria-hidden="true"
              className={cn(
                'relative h-5 w-9 rounded-full transition-colors',
                current === 'true' ? 'bg-accent' : 'bg-line',
              )}
            >
              <span
                className={cn(
                  'absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-all',
                  current === 'true' ? 'left-[18px]' : 'left-0.5',
                )}
              />
            </span>
            <span className="text-[13px] text-ink-secondary">
              {current === 'true' ? '已启用' : '已关闭'}
            </span>
          </label>
        ) : field.kind === 'select' ? (
          <select
            id={inputId}
            value={current}
            onChange={(event) => onChange(field.key, event.target.value)}
            className="w-full max-w-xs rounded-lg border border-line bg-paper px-3 py-2 text-[13px] text-ink transition-colors focus:border-accent focus:outline-none"
          >
            {field.options.map((option) => (
              <option key={option || '__empty'} value={option}>
                {option || '（留空）'}
              </option>
            ))}
          </select>
        ) : (
          <input
            id={inputId}
            type={field.kind === 'secret' ? 'password' : 'text'}
            inputMode={
              field.kind === 'int' ? 'numeric' : field.kind === 'float' ? 'decimal' : undefined
            }
            value={current}
            placeholder={placeholder}
            autoComplete={field.kind === 'secret' ? 'new-password' : 'off'}
            onChange={(event) => onChange(field.key, event.target.value)}
            className={cn(
              'w-full rounded-lg border border-line bg-paper px-3 py-2 text-[13px] text-ink',
              'transition-colors placeholder:text-muted/70 focus:border-accent focus:outline-none',
              field.kind === 'secret' && 'font-mono',
            )}
          />
        )}
      </div>
    </div>
  )
}

function SettingsCard({
  icon,
  title,
  body,
  footer,
  tone,
}: {
  icon: ReactNode
  title: string
  body: string
  footer?: string
  tone?: 'ok' | 'warn'
}) {
  return (
    <div className="card card-hover p-5">
      <div className="flex items-center gap-2 text-ink">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-soft text-accent">
          {icon}
        </span>
        <span className="text-[15px] font-medium">{title}</span>
      </div>
      <p className="mt-2.5 text-[14px] leading-relaxed text-muted">{body}</p>
      {footer ? (
        <div className="mt-3 flex items-center gap-1.5 text-[12px]">
          <span
            className={
              tone === 'ok'
                ? 'state-dot online'
                : tone === 'warn'
                  ? 'state-dot offline'
                  : 'state-dot checking'
            }
          />
          <span className="text-muted">{footer}</span>
        </div>
      ) : null}
    </div>
  )
}
