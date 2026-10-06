import { useEffect, useId, useRef, useState } from 'react'
import { Check, Eye, ShieldCheck, Zap } from 'lucide-react'

import { useAppStore } from '@/store/app-store'
import type { AccessMode } from '@/lib/types'
import { cn } from '@/lib/utils'

interface ModeSpec {
  value: AccessMode
  label: string
  hint: string
  Icon: typeof Eye
  /** Applied to the trigger label; mirrors the tone of the active policy. */
  tone: string
  /** Applied to the active menu row. */
  activeTone: string
}

/**
 * Three approval policies, ordered strictest to most permissive.
 *
 * These mirror `artpm_agent/security/access_mode.py`; the server owns the
 * enforcement, so the copy here only has to be honest about the consequence.
 */
const MODES: ModeSpec[] = [
  {
    value: 'read_only',
    label: '只读',
    hint: '仅执行查询，写入直接拒绝',
    Icon: Eye,
    tone: 'text-ink',
    activeTone: 'text-ink',
  },
  {
    value: 'controlled',
    label: '受控',
    hint: '每个写入操作都需你逐项确认',
    Icon: ShieldCheck,
    tone: 'text-warning',
    activeTone: 'text-warning',
  },
  {
    value: 'full_access',
    label: '全自动',
    hint: '低/中风险自动放行，高风险仍需确认',
    Icon: Zap,
    tone: 'text-accent',
    activeTone: 'text-accent',
  },
]

function specFor(mode: AccessMode): ModeSpec {
  return MODES.find((item) => item.value === mode) ?? MODES[1]
}

export function AccessModePicker() {
  const accessMode = useAppStore((state) => state.accessMode)
  const busy = useAppStore((state) => state.accessModeBusy)
  const changeAccessMode = useAppStore((state) => state.changeAccessMode)
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const menuId = useId()

  const current = specFor(accessMode)
  const { Icon } = current

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const choose = (mode: AccessMode) => {
    setOpen(false)
    if (mode !== accessMode) void changeAccessMode(mode)
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        disabled={busy}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`访问模式：${current.label}`}
        className={cn(
          'flex items-center gap-1 rounded-lg px-1.5 py-1 text-[12px] font-medium transition-colors',
          'hover:bg-surface focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
          busy ? 'cursor-wait opacity-60' : 'cursor-pointer',
          current.tone,
        )}
      >
        <Icon size={13} strokeWidth={2.2} aria-hidden="true" />
        <span>{current.label}</span>
      </button>

      {open ? (
        <div
          id={menuId}
          role="menu"
          aria-label="选择访问模式"
          className="absolute bottom-full left-0 z-30 mb-2 w-72 overflow-hidden rounded-xl border border-line bg-paper p-1 shadow-lg"
        >
          {MODES.map((mode) => {
            const active = mode.value === accessMode
            const RowIcon = mode.Icon
            return (
              <button
                key={mode.value}
                type="button"
                role="menuitemradio"
                aria-checked={active}
                onClick={() => choose(mode.value)}
                className={cn(
                  'flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors',
                  active ? 'bg-surface' : 'hover:bg-surface',
                )}
              >
                <RowIcon
                  size={15}
                  strokeWidth={2.2}
                  aria-hidden="true"
                  className={cn('mt-0.5 shrink-0', active ? mode.activeTone : 'text-muted')}
                />
                <span className="min-w-0 flex-1">
                  <span
                    className={cn(
                      'block text-[13px] font-semibold',
                      active ? mode.activeTone : 'text-ink',
                    )}
                  >
                    {mode.label}
                  </span>
                  <span className="mt-0.5 block text-[11.5px] leading-snug text-muted">
                    {mode.hint}
                  </span>
                </span>
                {active ? (
                  <Check
                    size={14}
                    strokeWidth={2.6}
                    aria-hidden="true"
                    className={cn('mt-0.5 shrink-0', mode.activeTone)}
                  />
                ) : null}
              </button>
            )
          })}
        </div>
      ) : null}
    </div>
  )
}
