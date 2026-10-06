/**
 * Application store — conversations, messages, connection state.
 *
 * Kept deliberately small: the gateway is the source of truth and every
 * mutation here is a projection of a gateway response.
 */
import { create } from 'zustand'

import {
  ApiError,
  deleteConversation,
  fetchAccessMode,
  fetchCapabilities,
  fetchConfig,
  fetchConversations,
  fetchMessages,
  fetchReadiness,
  sendChat,
  setAccessMode,
  setConversationArchived,
  updateConfig,
} from '@/lib/api'
import type {
  AccessMode,
  Capability,
  ConfigPayload,
  ConfigUpdateResult,
  ConnectionState,
  Conversation,
  Message,
} from '@/lib/types'

export type ViewKey = 'chat' | 'settings' | 'observability'

interface AppState {
  view: ViewKey
  connection: ConnectionState
  conversations: Conversation[]
  /** Archived conversations, loaded on demand so they stay recoverable. */
  archivedConversations: Conversation[]
  showArchived: boolean
  activeConversationId: string | null
  messages: Message[]
  capabilities: Capability[]
  /**
   * Effective approval policy of the active conversation. `controlled` is the
   * server's own fail-closed baseline, so it is also the safe local default.
   */
  accessMode: AccessMode
  /**
   * Mode chosen while no conversation existed yet. `/v1/chat` creates and
   * sends in one call, so the first turn of a brand-new conversation still runs
   * under the server default; this value is persisted immediately afterwards.
   */
  pendingAccessMode: AccessMode | null
  accessModeBusy: boolean
  loadingMessages: boolean
  sending: boolean
  error: string | null

  /**
   * Gateway `.env` schema with secrets masked, plus the operator's unsaved
   * edits. `draft` holds only the fields the operator actually touched, so a
   * save never overwrites a key the UI merely displayed.
   */
  config: ConfigPayload | null
  configDraft: Record<string, string>
  configLoading: boolean
  configSaving: boolean
  configResult: ConfigUpdateResult | null
  configError: string | null

  /** Conversation currently awaiting a destructive-action confirmation. */
  pendingDeleteId: string | null

  setView: (view: ViewKey) => void
  checkConnection: () => Promise<void>
  loadConversations: () => Promise<void>
  toggleArchivedView: () => Promise<void>
  loadCapabilities: () => Promise<void>
  selectConversation: (id: string | null) => Promise<void>
  submitMessage: (text: string) => Promise<void>
  changeAccessMode: (mode: AccessMode) => Promise<void>
  archiveConversation: (id: string, archived: boolean) => Promise<void>
  requestDeleteConversation: (id: string | null) => void
  confirmDeleteConversation: () => Promise<void>
  loadConfig: () => Promise<void>
  setConfigField: (key: string, value: string) => void
  discardConfigDraft: () => void
  saveConfig: () => Promise<void>
  clearError: () => void
}

export const useAppStore = create<AppState>((set, get) => ({
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

  setView: (view) => set({ view }),

  checkConnection: async () => {
    set({ connection: 'checking' })
    try {
      await fetchReadiness()
      set({ connection: 'online' })
    } catch {
      set({ connection: 'offline' })
    }
  },

  loadConversations: async () => {
    try {
      // Refresh both buckets together so an archive/restore is reflected in the
      // active list and the archive list without a second round trip later.
      const [conversations, archivedConversations] = await Promise.all([
        fetchConversations(),
        fetchConversations(true),
      ])
      set({ conversations, archivedConversations })
    } catch (error) {
      // A missing conversation list must not block the chat surface; the
      // status dot already communicates gateway reachability.
      if (error instanceof ApiError && error.code === 'network_error') {
        set({ connection: 'offline' })
      }
    }
  },

  toggleArchivedView: async () => {
    set({ showArchived: !get().showArchived })
    await get().loadConversations()
  },

  loadCapabilities: async () => {
    try {
      const capabilities = await fetchCapabilities()
      set({ capabilities })
    } catch {
      // Capabilities power optional quick actions only.
    }
  },

  selectConversation: async (id) => {
    set({ activeConversationId: id, messages: [], error: null })
    if (!id) {
      // Back to the empty state: fall back to the baseline the server would
      // use for a conversation that has no grant row yet.
      set({ accessMode: 'controlled', pendingAccessMode: null })
      return
    }
    set({ loadingMessages: true })
    try {
      const [messages, accessMode] = await Promise.all([
        fetchMessages(id),
        fetchAccessMode(id).catch(() => 'controlled' as AccessMode),
      ])
      // Ignore a stale response if the user switched conversations meanwhile.
      if (get().activeConversationId === id) {
        set({ messages, accessMode, pendingAccessMode: null })
      }
    } catch (error) {
      if (get().activeConversationId === id) {
        set({
          error:
            error instanceof ApiError ? error.message : '无法加载会话记录。',
        })
      }
    } finally {
      set({ loadingMessages: false })
    }
  },

  changeAccessMode: async (mode) => {
    const conversationId = get().activeConversationId
    const previous = get().accessMode
    if (!conversationId) {
      // No conversation yet, so there is nothing to persist against. Keep the
      // choice locally and apply it once the first turn creates the row.
      set({ accessMode: mode, pendingAccessMode: mode, error: null })
      return
    }
    set({ accessModeBusy: true, error: null })
    try {
      const applied = await setAccessMode(conversationId, mode)
      set({ accessMode: applied, pendingAccessMode: null })
    } catch (error) {
      // Never leave the UI claiming a policy the server did not accept.
      set({
        accessMode: previous,
        error:
          error instanceof ApiError
            ? error.message
            : '访问模式切换失败，请重试。',
      })
    } finally {
      set({ accessModeBusy: false })
    }
  },

  submitMessage: async (text) => {
    const trimmed = text.trim()
    if (!trimmed || get().sending) return
    const conversationId = get().activeConversationId ?? undefined
    const pending = get().pendingAccessMode
    const optimistic: Message = { role: 'user', content: trimmed }
    set({
      sending: true,
      error: null,
      messages: [...get().messages, optimistic],
    })
    try {
      const response = await sendChat(trimmed, conversationId)
      const assistant: Message = {
        role: 'assistant',
        content: response.response,
        status: response.success ? 'complete' : 'error',
        turn_id: response.turn_id,
      }
      set({
        messages: [...get().messages, assistant],
        activeConversationId: response.conversation_id,
      })
      // The first turn of a new conversation already ran under the server's
      // fail-closed default. Persist the operator's choice now so every later
      // turn in this conversation is governed by it.
      if (!conversationId && pending) {
        try {
          const applied = await setAccessMode(response.conversation_id, pending)
          set({ accessMode: applied, pendingAccessMode: null })
        } catch {
          set({ accessMode: 'controlled', pendingAccessMode: null })
        }
      }
      await get().loadConversations()
    } catch (error) {
      set({
        error: error instanceof ApiError ? error.message : '消息发送失败。',
      })
    } finally {
      set({ sending: false })
    }
  },

  archiveConversation: async (id, archived) => {
    try {
      await setConversationArchived(id, archived)
      // An archived conversation leaves the default listing, so drop the
      // selection rather than leaving the chat surface bound to a hidden row.
      if (archived && get().activeConversationId === id) {
        set({ activeConversationId: null, messages: [], accessMode: 'controlled' })
      }
      await get().loadConversations()
    } catch (error) {
      set({
        error:
          error instanceof ApiError
            ? error.message
            : archived
              ? '归档失败，请重试。'
              : '恢复失败，请重试。',
      })
    }
  },

  requestDeleteConversation: (id) => set({ pendingDeleteId: id }),

  confirmDeleteConversation: async () => {
    const id = get().pendingDeleteId
    if (!id) return
    set({ pendingDeleteId: null })
    try {
      await deleteConversation(id)
      if (get().activeConversationId === id) {
        set({ activeConversationId: null, messages: [], accessMode: 'controlled' })
      }
      await get().loadConversations()
    } catch (error) {
      set({
        error: error instanceof ApiError ? error.message : '删除失败，请重试。',
      })
    }
  },

  loadConfig: async () => {
    set({ configLoading: true, configError: null })
    try {
      const config = await fetchConfig()
      set({ config, configDraft: {}, configResult: null })
    } catch (error) {
      set({
        configError:
          error instanceof ApiError ? error.message : '无法加载网关配置。',
      })
    } finally {
      set({ configLoading: false })
    }
  },

  setConfigField: (key, value) =>
    set((state) => ({
      configDraft: { ...state.configDraft, [key]: value },
      // Any new edit invalidates the previous save's confirmation banner.
      configResult: null,
      configError: null,
    })),

  discardConfigDraft: () => set({ configDraft: {}, configError: null, configResult: null }),

  saveConfig: async () => {
    const draft = get().configDraft
    if (Object.keys(draft).length === 0) return
    set({ configSaving: true, configError: null, configResult: null })
    try {
      const result = await updateConfig(draft)
      set({ configResult: result, configDraft: {} })
      // Re-read so the masked previews reflect what the gateway actually stored.
      await get().loadConfig()
      set({ configResult: result })
    } catch (error) {
      set({
        configError:
          error instanceof ApiError ? error.message : '配置保存失败，请重试。',
      })
    } finally {
      set({ configSaving: false })
    }
  },

  clearError: () => set({ error: null }),
}))
