import { useEffect, useRef, useState } from 'react'
import { ArrowUp, Bot, CheckCircle2, Paperclip, Sparkles, Workflow } from 'lucide-react'

import { useAppStore } from '@/store/app-store'
import { AccessModePicker } from '@/components/access-mode-picker'
import { cn } from '@/lib/utils'

/** Quick actions mirror the Streamlit chat page welcome shortcuts. */
const QUICK_ACTIONS = [
  { label: '分析利润率', prompt: '帮我分析当前项目的利润率和成本结构' },
  { label: '创建报价', prompt: '帮我为这个项目创建一份报价单' },
  { label: '分配任务', prompt: '帮我把当前任务分配给团队成员' },
  { label: '检查进度', prompt: '帮我检查项目进度并预警风险' },
  { label: '评估需求', prompt: '帮我评估这份需求的工时和成本' },
  { label: '质量控制', prompt: '帮我做一次交付质量检查' },
  { label: '复盘总结', prompt: '帮我总结这个项目的复盘要点' },
]

export function ChatPage() {
  const messages = useAppStore((state) => state.messages)
  const sending = useAppStore((state) => state.sending)
  const error = useAppStore((state) => state.error)
  const activeId = useAppStore((state) => state.activeConversationId)
  const conversations = useAppStore((state) => state.conversations)
  const submitMessage = useAppStore((state) => state.submitMessage)
  const clearError = useAppStore((state) => state.clearError)
  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const node = scrollRef.current
    if (!node) return
    if (messages.length === 0 && !sending) {
      node.scrollTop = 0
      return
    }
    node.scrollTop = node.scrollHeight
  }, [messages, sending])

  const send = () => {
    const text = draft.trim()
    if (!text || sending) return
    setDraft('')
    void submitMessage(text)
  }

  return (
    <div className="chat-workspace flex h-full min-h-0 flex-col">
      <header className="workspace-topbar flex items-center justify-between border-b border-line-light px-6 py-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[12px] font-medium text-muted">
            <Workflow size={14} className="text-accent" />
            <span>项目协作 / 智能工作台</span>
          </div>
          <h1 className="mt-1 truncate text-[18px] font-semibold tracking-[-0.02em] text-ink">
            {activeId
              ? conversations.find((item) => item.id === activeId)?.title || '当前项目会话'
              : '项目决策助手'}
          </h1>
        </div>
        <div className="hidden items-center gap-2 text-[12px] text-muted sm:flex">
          <span className="state-dot online" />
          本地工作区已就绪
        </div>
      </header>

      {/* 消息区 */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6">
          {messages.length === 0 && !activeId ? (
            <WelcomeView
              onPick={(prompt) => {
                setDraft('')
                void submitMessage(prompt)
              }}
            />
          ) : (
            <div className="space-y-5">
              {messages.map((message, index) => (
                <MessageBubble
                  key={`${message.role}-${message.id ?? index}`}
                  role={message.role}
                  content={message.content}
                />
              ))}
              {sending ? <ThinkingBubble /> : null}
            </div>
          )}
        </div>
      </div>

      {/* 错误条 */}
      {error ? (
        <div className="mx-auto w-full max-w-5xl px-4 pb-3 sm:px-6">
          <div className="flex items-center justify-between rounded-lg border border-danger/30 bg-danger-soft px-3.5 py-2.5 text-sm text-ink">
            <span className="min-w-0 flex-1 truncate">{error}</span>
            <button
              type="button"
              onClick={clearError}
              className="ml-3 flex-none text-sm font-medium text-accent hover:text-accent-hover"
            >
              关闭
            </button>
          </div>
        </div>
      ) : null}

      {/* 输入区 */}
      <div className="composer-dock border-t border-line-light bg-canvas px-4 pb-5 pt-4 sm:px-6">
        <div className="mx-auto w-full max-w-5xl">
          <div className="composer-shell rounded-2xl border border-line bg-paper shadow-sm transition-shadow focus-within:border-accent/50 focus-within:shadow-md">
            <textarea
              ref={inputRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  send()
                }
              }}
              rows={2}
              placeholder="描述你的项目问题，或让 ArtPM 帮你推进下一步…"
              className="max-h-40 w-full resize-none bg-transparent px-4 pt-4 text-[15px] leading-relaxed text-ink outline-none placeholder:text-muted"
            />
            <div className="flex items-center justify-between px-3 pb-3 pt-1">
              <div className="flex items-center gap-2 text-[12px] text-muted">
                <button type="button" className="composer-tool" aria-label="添加附件">
                  <Paperclip size={15} />
                </button>
                <span className="hidden sm:inline">Shift + Enter 换行</span>
                <AccessModePicker />
              </div>
              <button
                type="button"
                onClick={send}
                disabled={!draft.trim() || sending}
                aria-label="发送"
                className={cn(
                  'flex h-9 w-9 items-center justify-center rounded-xl transition-colors',
                  draft.trim() && !sending
                    ? 'bg-accent text-white hover:bg-accent-hover'
                    : 'cursor-not-allowed bg-surface text-muted',
                )}
              >
                <ArrowUp size={16} strokeWidth={2.4} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function WelcomeView({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="welcome-stage animate-fade-in">
      <div className="welcome-kicker">
        <span className="welcome-icon"><Sparkles size={17} /></span>
        <span>ArtPM intelligence layer</span>
      </div>
      <h1 className="welcome-title mt-5 max-w-3xl text-[clamp(22px,4vw,44px)] font-semibold leading-[1.14] tracking-[-0.045em] text-ink">
        <span className="[text-wrap:nowrap]">把项目里的复杂问题，</span>
        <span className="text-accent [text-wrap:nowrap]">变成下一步行动。</span>
      </h1>
      <p className="mt-4 max-w-xl text-[15px] leading-7 text-muted">
        从报价、任务分配到进度预警和交付复盘，围绕当前项目持续协作。
      </p>
      <div className="mt-8 grid w-full max-w-3xl gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {QUICK_ACTIONS.map(({ label, prompt }) => (
          <button
            key={label}
            type="button"
            onClick={() => onPick(prompt)}
            className={cn(
              'quick-action flex items-center justify-between rounded-xl border border-line-light bg-paper px-4 py-3 text-left',
              'text-[13px] font-medium text-ink-secondary transition-colors',
              'hover:border-accent/35 hover:bg-accent-soft hover:text-accent',
            )}
          >
            {label}
            <ArrowUp size={14} className="rotate-45 text-muted" />
          </button>
        ))}
      </div>
    </div>
  )
}

function MessageBubble({
  role,
  content,
}: {
  role: string
  content: string
}) {
  const isUser = role === 'user'
  return (
    <div className={cn('message-row flex animate-fade-in gap-3', isUser && 'flex-row-reverse')}>
      {!isUser ? (
        <div className="agent-avatar mt-0.5 flex h-8 w-8 flex-none items-center justify-center rounded-xl bg-ink text-white">
          <Bot size={15} />
        </div>
      ) : null}
      <div
        className={cn(
          'min-w-0 max-w-[85%] rounded-2xl px-4 py-3 text-[15px] leading-relaxed whitespace-pre-wrap',
          isUser
            ? 'border border-chat-border bg-chat-user-bg text-chat-user-fg shadow-sm'
            : 'assistant-message text-ink-secondary',
        )}
      >
        {content}
      </div>
    </div>
  )
}

function ThinkingBubble() {
  return (
    <div className="flex animate-fade-in gap-3">
      <div className="agent-avatar mt-0.5 flex h-8 w-8 flex-none items-center justify-center rounded-xl bg-ink text-white">
        <Bot size={15} />
      </div>
      <div className="flex items-center gap-1.5 px-1 py-3 text-[15px] text-muted">
        <CheckCircle2 size={15} className="text-accent" />
        <span className="animate-pulse-soft">正在整理项目上下文</span>
        <span className="flex gap-1">
          {[0, 1, 2].map((index) => (
            <span
              key={index}
              className="h-1 w-1 animate-pulse-soft rounded-full bg-muted"
              style={{ animationDelay: `${index * 0.18}s` }}
            />
          ))}
        </span>
      </div>
    </div>
  )
}
