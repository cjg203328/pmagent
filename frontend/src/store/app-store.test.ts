/**
 * Store contract tests for the flows the gateway depends on being correct.
 *
 * These run without a DOM: the store is a pure projection of gateway responses,
 * so the API module is mocked and every assertion is made against observable
 * store state. The race and rollback cases are the ones that silently corrupt
 * the UI when they regress, so they are covered explicitly.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', () => {
  class ApiError extends Error {
    code: string
    status: number

    constructor(status: number, code: string, message: string) {
      super(message)
      this.name = 'ApiError'
      this.code = code
      this.status = status
    }
  }

  return {
    ApiError,
    WORKSPACE_ID: 'local-default',
    ACTOR_ID: 'local-ui',
    TENANT_ID: 'local',
    fetchReadiness: vi.fn(),
    fetchCapabilities: vi.fn(),
    fetchConversations: vi.fn(),
    fetchMessages: vi.fn(),
    sendChat: vi.fn(),
    fetchAccessMode: vi.fn(),
    setAccessMode: vi.fn(),
    setConversationArchived: vi.fn(),
    deleteConversation: vi.fn(),
    fetchConfig: vi.fn(),
    updateConfig: vi.fn(),
  }
})

import * as api from '@/lib/api'
import type { Message } from '@/lib/types'
import { useAppStore } from '@/store/app-store'

/** Restore the documented initial state between cases. */
function resetStore(): void {
  useAppStore.setState({
    view: 'chat',
    connection: 'checking',
    conversations: [],
    archivedConversations: [],
    showArchived: false,
    activeConversationId: null,
    messages: [],
    capabilities: [],
    accessMode: 'controlled',
    pendingAccessMode: null,
    accessModeBusy: false,
    loadingMessages: false,
    sending: false,
    error: null,
    config: null,
    configDraft: {},
    configLoading: false,
    configSaving: false,
    configResult: null,
    configError: null,
    pendingDeleteId: null,
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  resetStore()
  // Most flows refresh the conversation list; default it to a no-op success so
  // each case only has to mock what it actually exercises.
  vi.mocked(api.fetchConversations).mockResolvedValue([])
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('selectConversation', () => {
  it('ignores a stale response once the user switched to another conversation', async () => {
    let releaseSlow: (messages: Message[]) => void = () => {}
    const slow = new Promise<Message[]>((resolve) => {
      releaseSlow = resolve
    })

    vi.mocked(api.fetchMessages).mockImplementation((id) =>
      id === 'conv-a'
        ? slow
        : Promise.resolve([{ role: 'assistant', content: 'B reply' }]),
    )
    vi.mocked(api.fetchAccessMode).mockResolvedValue('controlled')

    const first = useAppStore.getState().selectConversation('conv-a')
    const second = useAppStore.getState().selectConversation('conv-b')
    await second

    releaseSlow([{ role: 'assistant', content: 'A reply' }])
    await first

    const state = useAppStore.getState()
    expect(state.activeConversationId).toBe('conv-b')
    expect(state.messages).toEqual([{ role: 'assistant', content: 'B reply' }])
    expect(state.error).toBeNull()
  })

  it('falls back to the server baseline when the access-mode read fails', async () => {
    vi.mocked(api.fetchMessages).mockResolvedValue([])
    vi.mocked(api.fetchAccessMode).mockRejectedValue(new Error('boom'))

    await useAppStore.getState().selectConversation('conv-a')

    expect(useAppStore.getState().accessMode).toBe('controlled')
  })

  it('resets to the empty state when the selection is cleared', async () => {
    useAppStore.setState({ accessMode: 'full_access', pendingAccessMode: 'full_access' })

    await useAppStore.getState().selectConversation(null)

    const state = useAppStore.getState()
    expect(state.activeConversationId).toBeNull()
    expect(state.messages).toEqual([])
    expect(state.accessMode).toBe('controlled')
    expect(state.pendingAccessMode).toBeNull()
  })

  it('surfaces the gateway message when loading messages fails', async () => {
    vi.mocked(api.fetchMessages).mockRejectedValue(
      new api.ApiError(403, 'forbidden', '无权访问该会话。'),
    )
    vi.mocked(api.fetchAccessMode).mockResolvedValue('controlled')

    await useAppStore.getState().selectConversation('conv-a')

    expect(useAppStore.getState().error).toBe('无权访问该会话。')
    expect(useAppStore.getState().loadingMessages).toBe(false)
  })
})

describe('changeAccessMode', () => {
  it('keeps the choice local while no conversation exists yet', async () => {
    await useAppStore.getState().changeAccessMode('read_only')

    const state = useAppStore.getState()
    expect(state.accessMode).toBe('read_only')
    expect(state.pendingAccessMode).toBe('read_only')
    // Nothing to persist against, so the gateway must not be called.
    expect(api.setAccessMode).not.toHaveBeenCalled()
  })

  it('rolls back and reports the error when the gateway rejects the change', async () => {
    useAppStore.setState({ activeConversationId: 'conv-a', accessMode: 'controlled' })
    vi.mocked(api.setAccessMode).mockRejectedValue(
      new api.ApiError(400, 'invalid_mode', '不允许的模式。'),
    )

    await useAppStore.getState().changeAccessMode('full_access')

    const state = useAppStore.getState()
    // The UI must never claim a policy the server did not accept.
    expect(state.accessMode).toBe('controlled')
    expect(state.error).toBe('不允许的模式。')
    expect(state.accessModeBusy).toBe(false)
  })

  it('adopts the mode the server echoes back on success', async () => {
    useAppStore.setState({ activeConversationId: 'conv-a', accessMode: 'controlled' })
    vi.mocked(api.setAccessMode).mockResolvedValue('read_only')

    await useAppStore.getState().changeAccessMode('read_only')

    const state = useAppStore.getState()
    expect(state.accessMode).toBe('read_only')
    expect(state.pendingAccessMode).toBeNull()
    expect(state.accessModeBusy).toBe(false)
  })
})

describe('submitMessage', () => {
  it('shows the user message immediately and appends the reply', async () => {
    vi.mocked(api.sendChat).mockResolvedValue({
      conversation_id: 'conv-new',
      turn_id: 'turn-1',
      response: '已完成。',
      success: true,
    })

    await useAppStore.getState().submitMessage('  项目进度如何  ')

    const state = useAppStore.getState()
    expect(state.messages).toEqual([
      { role: 'user', content: '项目进度如何' },
      {
        role: 'assistant',
        content: '已完成。',
        status: 'complete',
        turn_id: 'turn-1',
      },
    ])
    expect(state.activeConversationId).toBe('conv-new')
    expect(state.sending).toBe(false)
  })

  it('ignores blank input and never calls the gateway', async () => {
    await useAppStore.getState().submitMessage('   ')

    expect(api.sendChat).not.toHaveBeenCalled()
    expect(useAppStore.getState().messages).toEqual([])
  })

  it('keeps the optimistic message and reports the failure when sending fails', async () => {
    vi.mocked(api.sendChat).mockRejectedValue(
      new api.ApiError(0, 'network_error', '无法连接本地网关。'),
    )

    await useAppStore.getState().submitMessage('你好')

    const state = useAppStore.getState()
    expect(state.messages).toEqual([{ role: 'user', content: '你好' }])
    expect(state.error).toBe('无法连接本地网关。')
    expect(state.sending).toBe(false)
  })

  it('persists a pending mode once the first turn creates the conversation', async () => {
    useAppStore.setState({ pendingAccessMode: 'read_only' })
    vi.mocked(api.sendChat).mockResolvedValue({
      conversation_id: 'conv-new',
      turn_id: 'turn-1',
      response: '好的。',
      success: true,
    })
    vi.mocked(api.setAccessMode).mockResolvedValue('read_only')

    await useAppStore.getState().submitMessage('开始吧')

    expect(api.setAccessMode).toHaveBeenCalledWith('conv-new', 'read_only')
    const state = useAppStore.getState()
    expect(state.accessMode).toBe('read_only')
    expect(state.pendingAccessMode).toBeNull()
  })

  it('falls back to the controlled baseline if persisting the pending mode fails', async () => {
    useAppStore.setState({ pendingAccessMode: 'full_access' })
    vi.mocked(api.sendChat).mockResolvedValue({
      conversation_id: 'conv-new',
      turn_id: 'turn-1',
      response: '好的。',
      success: true,
    })
    vi.mocked(api.setAccessMode).mockRejectedValue(new Error('rejected'))

    await useAppStore.getState().submitMessage('开始吧')

    const state = useAppStore.getState()
    expect(state.accessMode).toBe('controlled')
    expect(state.pendingAccessMode).toBeNull()
  })
})

describe('archive and delete', () => {
  it('clears the selection when the active conversation is archived', async () => {
    useAppStore.setState({
      activeConversationId: 'conv-a',
      messages: [{ role: 'user', content: 'hi' }],
      accessMode: 'full_access',
    })
    vi.mocked(api.setConversationArchived).mockResolvedValue({
      id: 'conv-a',
      title: 'A',
    })

    await useAppStore.getState().archiveConversation('conv-a', true)

    const state = useAppStore.getState()
    expect(state.activeConversationId).toBeNull()
    expect(state.messages).toEqual([])
    expect(state.accessMode).toBe('controlled')
  })

  it('keeps the selection when a different conversation is archived', async () => {
    useAppStore.setState({
      activeConversationId: 'conv-a',
      messages: [{ role: 'user', content: 'hi' }],
    })
    vi.mocked(api.setConversationArchived).mockResolvedValue({
      id: 'conv-b',
      title: 'B',
    })

    await useAppStore.getState().archiveConversation('conv-b', true)

    expect(useAppStore.getState().activeConversationId).toBe('conv-a')
  })

  it('reports a restore failure distinctly from an archive failure', async () => {
    vi.mocked(api.setConversationArchived).mockRejectedValue(new Error('boom'))

    await useAppStore.getState().archiveConversation('conv-a', false)

    expect(useAppStore.getState().error).toBe('恢复失败，请重试。')
  })

  it('deletes the pending conversation and clears it from the selection', async () => {
    useAppStore.setState({
      activeConversationId: 'conv-a',
      messages: [{ role: 'user', content: 'hi' }],
      pendingDeleteId: 'conv-a',
    })
    vi.mocked(api.deleteConversation).mockResolvedValue(undefined)

    await useAppStore.getState().confirmDeleteConversation()

    expect(api.deleteConversation).toHaveBeenCalledWith('conv-a')
    const state = useAppStore.getState()
    expect(state.pendingDeleteId).toBeNull()
    expect(state.activeConversationId).toBeNull()
    expect(state.messages).toEqual([])
  })

  it('does nothing when no delete was requested', async () => {
    await useAppStore.getState().confirmDeleteConversation()

    expect(api.deleteConversation).not.toHaveBeenCalled()
  })
})

describe('connection and config', () => {
  it('marks the gateway offline when the readiness probe fails', async () => {
    vi.mocked(api.fetchReadiness).mockRejectedValue(new Error('down'))

    await useAppStore.getState().checkConnection()

    expect(useAppStore.getState().connection).toBe('offline')
  })

  it('marks the gateway offline when the conversation list hits a network error', async () => {
    vi.mocked(api.fetchConversations).mockRejectedValue(
      new api.ApiError(0, 'network_error', '无法连接本地网关。'),
    )

    await useAppStore.getState().loadConversations()

    expect(useAppStore.getState().connection).toBe('offline')
  })

  it('only sends the fields the operator actually edited', async () => {
    vi.mocked(api.updateConfig).mockResolvedValue({
      applied: ['ARTPM_MODEL'],
      env_path: '.env',
      hot_reloaded: true,
      restart_required: false,
    })
    vi.mocked(api.fetchConfig).mockResolvedValue({
      env_path: '.env',
      groups: [],
      provider: 'deepseek',
      model: 'deepseek-chat',
    })

    useAppStore.getState().setConfigField('ARTPM_MODEL', 'deepseek-reasoner')
    await useAppStore.getState().saveConfig()

    expect(api.updateConfig).toHaveBeenCalledWith({ ARTPM_MODEL: 'deepseek-reasoner' })
    const state = useAppStore.getState()
    expect(state.configDraft).toEqual({})
    expect(state.configResult?.applied).toEqual(['ARTPM_MODEL'])
    expect(state.configSaving).toBe(false)
  })

  it('does not call the gateway when there is nothing to save', async () => {
    await useAppStore.getState().saveConfig()

    expect(api.updateConfig).not.toHaveBeenCalled()
  })

  it('clears a stale save banner as soon as a new edit is made', async () => {
    useAppStore.setState({
      configResult: {
        applied: ['ARTPM_MODEL'],
        env_path: '.env',
        hot_reloaded: true,
        restart_required: false,
      },
    })

    useAppStore.getState().setConfigField('ARTPM_MODEL', 'other')

    expect(useAppStore.getState().configResult).toBeNull()
  })
})
