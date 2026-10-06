import { useEffect, useRef, useState } from 'react'
import {
  Activity,
  Archive,
  ArchiveRestore,
  FolderKanban,
  MessageSquare,
  MoreHorizontal,
  Plus,
  Settings,
  Trash2,
} from 'lucide-react'

import { useAppStore, type ViewKey } from '@/store/app-store'
import { cn, formatTime } from '@/lib/utils'

const NAV_ITEMS: { key: ViewKey; label: string; icon: typeof MessageSquare }[] = [
  { key: 'chat', label: '对话', icon: MessageSquare },
  { key: 'settings', label: '设置', icon: Settings },
  { key: 'observability', label: '可观测', icon: Activity },
]

const CONNECTION_LABEL = {
  checking: '正在检测服务…',
  online: 'API 服务可达 · UI 本地直连',
  offline: 'API 服务不可达',
} as const

export function Sidebar() {
  const view = useAppStore((state) => state.view)
  const setView = useAppStore((state) => state.setView)
  const connection = useAppStore((state) => state.connection)
  const conversations = useAppStore((state) => state.conversations)
  const archivedConversations = useAppStore((state) => state.archivedConversations)
  const showArchived = useAppStore((state) => state.showArchived)
  const toggleArchivedView = useAppStore((state) => state.toggleArchivedView)
  const activeId = useAppStore((state) => state.activeConversationId)
  const selectConversation = useAppStore((state) => state.selectConversation)
  const archiveConversation = useAppStore((state) => state.archiveConversation)
  const requestDeleteConversation = useAppStore((state) => state.requestDeleteConversation)
  const checkConnection = useAppStore((state) => state.checkConnection)

  const [menuId, setMenuId] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void checkConnection()
    const timer = window.setInterval(() => void checkConnection(), 30_000)
    return () => window.clearInterval(timer)
  }, [checkConnection])

  useEffect(() => {
    if (!menuId) return
    const onPointerDown = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuId(null)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuId(null)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [menuId])

  const items = showArchived ? archivedConversations : conversations

  return (
    <aside className="workspace-sidebar flex h-full w-[272px] flex-none flex-col border-r border-line-light bg-sidebar">
      {/* 品牌区 */}
      <div className="flex items-center gap-3 px-5 pb-5 pt-5">
        <div className="brand-mark flex h-9 w-9 items-center justify-center rounded-xl bg-accent text-white shadow-sm">
          <FolderKanban size={18} strokeWidth={2.2} />
        </div>
        <div className="min-w-0">
          <div className="truncate text-[16px] font-semibold leading-tight tracking-[-0.02em] text-ink">
            ArtPM Agent
          </div>
          <div className="mt-0.5 text-[12px] text-muted">项目协作工作台</div>
        </div>
      </div>

      {/* 新建会话 */}
      <div className="px-4 pb-4">
        <button
          type="button"
          onClick={() => {
            setView('chat')
            void selectConversation(null)
          }}
          className={cn(
            'flex w-full items-center justify-center gap-2 rounded-xl bg-accent px-3 py-3',
            'text-[14px] font-semibold text-white shadow-sm transition-colors',
            'hover:bg-accent-hover active:scale-[0.99]',
          )}
        >
          <Plus size={16} strokeWidth={2.4} />
          新建会话
        </button>
      </div>

      {/* 会话列表（最近 / 已归档） */}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-3">
        <div className="flex items-center justify-between px-1 pb-2">
          <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
            {showArchived ? '已归档' : '最近会话'}
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] tabular-nums text-muted">{items.length}</span>
            <button
              type="button"
              onClick={() => void toggleArchivedView()}
              aria-pressed={showArchived}
              aria-label={showArchived ? '返回最近会话' : '查看已归档会话'}
              title={showArchived ? '返回最近会话' : '查看已归档会话'}
              className={cn(
                'flex h-5 w-5 items-center justify-center rounded transition-colors',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent',
                showArchived
                  ? 'bg-accent-soft text-accent'
                  : 'text-muted hover:bg-sidebar-hover hover:text-ink',
              )}
            >
              <Archive size={12} strokeWidth={2.2} />
            </button>
          </div>
        </div>

        {items.length === 0 ? (
          <div className="px-1 py-2 text-[13px] leading-relaxed text-muted">
            {showArchived
              ? '暂无归档会话。在会话上打开菜单即可归档。'
              : '暂无会话记录。发送第一条消息后会自动创建。'}
          </div>
        ) : (
          <ul className="space-y-0.5">
            {items.map((conversation) => {
              const active = conversation.id === activeId && view === 'chat'
              const menuOpen = menuId === conversation.id
              return (
                <li key={conversation.id} className="relative">
                  <div
                    className={cn(
                      'group flex w-full items-center gap-1 rounded-lg pl-2.5 pr-1 transition-colors',
                      active
                        ? 'bg-sidebar-active shadow-sm ring-1 ring-line-light'
                        : 'hover:bg-sidebar-hover',
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => {
                        setView('chat')
                        void selectConversation(conversation.id)
                      }}
                      className={cn(
                        'min-w-0 flex-1 truncate py-2.5 text-left text-[13px]',
                        active ? 'font-medium text-ink' : 'text-ink-secondary',
                      )}
                      title={conversation.title || '未命名会话'}
                    >
                      {conversation.title || '未命名会话'}
                    </button>
                    <span
                      className={cn(
                        'flex-none text-[11px] text-muted opacity-70 transition-opacity',
                        // The row menu replaces the timestamp on hover so the
                        // title keeps its full width until the user acts.
                        menuOpen ? 'opacity-0' : 'group-hover:opacity-0',
                      )}
                    >
                      {formatTime(conversation.updated_at)}
                    </span>
                    <button
                      type="button"
                      onClick={() => setMenuId(menuOpen ? null : conversation.id)}
                      aria-haspopup="menu"
                      aria-expanded={menuOpen}
                      aria-label={`会话操作：${conversation.title || '未命名会话'}`}
                      className={cn(
                        'flex h-6 w-6 flex-none items-center justify-center rounded transition-all',
                        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent',
                        menuOpen
                          ? 'bg-sidebar-active text-ink opacity-100'
                          : 'text-muted opacity-0 hover:text-ink group-hover:opacity-100 focus-visible:opacity-100',
                      )}
                    >
                      <MoreHorizontal size={14} strokeWidth={2.2} />
                    </button>
                  </div>

                  {menuOpen ? (
                    <div
                      ref={menuRef}
                      role="menu"
                      className="absolute right-1 top-full z-30 mt-1 w-40 overflow-hidden rounded-lg border border-line bg-paper p-1 shadow-lg"
                    >
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setMenuId(null)
                          void archiveConversation(conversation.id, !showArchived)
                        }}
                        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] text-ink transition-colors hover:bg-surface"
                      >
                        {showArchived ? (
                          <ArchiveRestore size={14} strokeWidth={2.2} className="text-muted" />
                        ) : (
                          <Archive size={14} strokeWidth={2.2} className="text-muted" />
                        )}
                        {showArchived ? '恢复会话' : '归档会话'}
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setMenuId(null)
                          requestDeleteConversation(conversation.id)
                        }}
                        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] text-danger transition-colors hover:bg-danger-soft"
                      >
                        <Trash2 size={14} strokeWidth={2.2} />
                        删除会话
                      </button>
                    </div>
                  ) : null}
                </li>
              )
            })}
          </ul>
        )}
      </div>

      {/* 工作区导航 */}
      <div className="border-t border-line-light px-4 py-4">
        <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
          工作区
        </div>
        <nav className="grid grid-cols-3 gap-1">
          {NAV_ITEMS.map(({ key, label, icon: Icon }) => {
            const active = view === key
            return (
              <button
                key={key}
                type="button"
                onClick={() => setView(key)}
                className={cn(
                  'flex min-w-0 flex-col items-center gap-1 rounded-lg px-1 py-2.5',
                  'text-[12px] whitespace-nowrap transition-colors',
                  active
                    ? 'bg-sidebar-active font-medium text-accent shadow-sm ring-1 ring-line-light'
                    : 'text-ink-secondary hover:bg-sidebar-hover',
                )}
              >
                <Icon size={16} />
                {label}
              </button>
            )
          })}
        </nav>
        <div className="mt-2.5 flex items-center gap-1.5 px-1 text-[12px] text-muted">
          <span
            className={cn(
              'state-dot',
              connection === 'online' && 'online',
              connection === 'offline' && 'offline',
              connection === 'checking' && 'checking animate-pulse-soft',
            )}
          />
          <span className="truncate">{CONNECTION_LABEL[connection]}</span>
        </div>
      </div>

      <DeleteConversationDialog />
    </aside>
  )
}

/**
 * Confirmation for the one irreversible conversation action.
 *
 * Deleting drops the conversation, its messages and its access grant, so it is
 * never triggered from a single click in the row menu.
 */
function DeleteConversationDialog() {
  const pendingDeleteId = useAppStore((state) => state.pendingDeleteId)
  const requestDeleteConversation = useAppStore((state) => state.requestDeleteConversation)
  const confirmDeleteConversation = useAppStore((state) => state.confirmDeleteConversation)
  const conversations = useAppStore((state) => state.conversations)
  const archivedConversations = useAppStore((state) => state.archivedConversations)

  useEffect(() => {
    if (!pendingDeleteId) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') requestDeleteConversation(null)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [pendingDeleteId, requestDeleteConversation])

  if (!pendingDeleteId) return null

  const target = [...conversations, ...archivedConversations].find(
    (item) => item.id === pendingDeleteId,
  )

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/20 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) requestDeleteConversation(null)
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="删除会话确认"
        className="w-full max-w-sm rounded-xl border border-line bg-paper p-5 shadow-lg"
      >
        <div className="flex items-center gap-2 text-ink">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-danger-soft text-danger">
            <Trash2 size={16} strokeWidth={2.2} />
          </span>
          <span className="text-[15px] font-semibold">删除这个会话？</span>
        </div>
        <p className="mt-2.5 text-[13px] leading-relaxed text-muted">
          {target?.title ? (
            <>
              「<span className="text-ink">{target.title}</span>」及其全部消息将被永久删除，
            </>
          ) : (
            '该会话及其全部消息将被永久删除，'
          )}
          此操作不可撤销。如需保留记录，请改用「归档会话」。
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => requestDeleteConversation(null)}
            className="rounded-lg px-3 py-2 text-[13px] font-medium text-ink-secondary transition-colors hover:bg-surface"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void confirmDeleteConversation()}
            className="rounded-lg bg-danger px-3 py-2 text-[13px] font-semibold text-white transition-colors hover:opacity-90"
          >
            永久删除
          </button>
        </div>
      </div>
    </div>
  )
}
