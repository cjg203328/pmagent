/**
 * ArtPM Gateway API client.
 *
 * Requests go through the Vite dev proxy (`/api` -> `http://127.0.0.1:8765`),
 * so the browser talks to the same origin and the explicit gateway CORS
 * allowlist stays untouched for direct calls. Identity headers mirror the
 * loopback development contract in artpm_agent/api/services.py.
 */
import type {
  AccessMode,
  AccessModeGrant,
  Capability,
  ChatResponse,
  ConfigPayload,
  ConfigUpdateResult,
  Conversation,
  GatewayErrorShape,
  Message,
  ReadinessPayload,
} from './types'

export const WORKSPACE_ID = 'local-default'
export const ACTOR_ID = 'local-ui'
export const TENANT_ID = 'local'

const API_BASE = '/api'

export class ApiError extends Error {
  code: string
  status: number

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

function identityHeaders(): Record<string, string> {
  return {
    'x-workspace-id': WORKSPACE_ID,
    'x-actor-id': ACTOR_ID,
    'x-actor-role': 'user',
    'x-actor-kind': 'human',
    'x-tenant-id': TENANT_ID,
  }
}

/**
 * Identity headers for the configuration routes, which require the admin role.
 *
 * This is the same loopback development contract as `identityHeaders`: the
 * trusted ingress injects identity in production, and the gateway refuses an
 * empty shared secret through a proxy there.
 */
function adminHeaders(): Record<string, string> {
  return { ...identityHeaders(), 'x-actor-role': 'admin' }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = 120_000,
): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...identityHeaders(),
        ...(init.headers ?? {}),
      },
    })
    const text = await response.text()
    const payload = text ? (JSON.parse(text) as unknown) : {}
    if (!response.ok) {
      const shape = payload as GatewayErrorShape
      throw new ApiError(
        response.status,
        shape.error?.code ?? `http_${response.status}`,
        shape.error?.message ?? `请求失败（HTTP ${response.status}）`,
      )
    }
    return payload as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(0, 'timeout', '请求超时，请稍后重试。')
    }
    throw new ApiError(0, 'network_error', '无法连接本地网关，请确认 API 服务已启动。')
  } finally {
    window.clearTimeout(timer)
  }
}

/** Health probe used by the connection status dot. */
export async function fetchReadiness(): Promise<ReadinessPayload> {
  return request<ReadinessPayload>('/ready', { method: 'GET' }, 10_000)
}

/** Workspace capability registry (drives the quick-action buttons). */
export async function fetchCapabilities(): Promise<Capability[]> {
  const payload = await request<{ items: Capability[] }>(
    '/v1/capabilities',
    { method: 'GET' },
    20_000,
  )
  return payload.items ?? []
}

/** Conversations, most recently updated first. */
export async function fetchConversations(archived = false): Promise<Conversation[]> {
  const payload = await request<{ items: Conversation[] }>(
    `/v1/conversations${archived ? '?archived=true' : ''}`,
    { method: 'GET' },
    20_000,
  )
  return payload.items ?? []
}

/** Chronological messages of one conversation. */
export async function fetchMessages(conversationId: string): Promise<Message[]> {
  const payload = await request<{ items: Message[] }>(
    `/v1/conversations/${encodeURIComponent(conversationId)}/messages`,
    { method: 'GET' },
    20_000,
  )
  return payload.items ?? []
}

/** One chat turn. */
export async function sendChat(
  message: string,
  conversationId?: string,
): Promise<ChatResponse> {
  return request<ChatResponse>('/v1/chat', {
    method: 'POST',
    body: JSON.stringify({
      message,
      ...(conversationId ? { conversation_id: conversationId } : {}),
    }),
  })
}

/**
 * Effective access mode of one conversation.
 *
 * A missing, expired, or differently-bound grant reads as `controlled`, which
 * is the safe baseline that still confirms every side-effecting action.
 */
export async function fetchAccessMode(conversationId: string): Promise<AccessMode> {
  const payload = await request<AccessModeGrant>(
    `/v1/conversations/${encodeURIComponent(conversationId)}/access-mode`,
    { method: 'GET' },
    20_000,
  )
  return payload.mode ?? 'controlled'
}

/** Persist the conversation access mode chosen by a human operator. */
export async function setAccessMode(
  conversationId: string,
  mode: AccessMode,
): Promise<AccessMode> {
  const payload = await request<AccessModeGrant>(
    `/v1/conversations/${encodeURIComponent(conversationId)}/access-mode`,
    { method: 'PUT', body: JSON.stringify({ mode }) },
    20_000,
  )
  return payload.mode ?? mode
}

/**
 * Archive or restore one conversation (a soft delete).
 *
 * Archived rows leave the default sidebar listing but stay recoverable, which
 * is why this is separate from `deleteConversation`.
 */
export async function setConversationArchived(
  conversationId: string,
  archived: boolean,
): Promise<Conversation> {
  const payload = await request<{ item: Conversation }>(
    `/v1/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'PATCH', body: JSON.stringify({ archived }) },
    20_000,
  )
  return payload.item
}

/** Permanently delete one conversation and its dependent local resources. */
export async function deleteConversation(conversationId: string): Promise<void> {
  await request<Record<string, never>>(
    `/v1/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'DELETE' },
    20_000,
  )
}

/** Allowlisted gateway configuration, with every secret masked. */
export async function fetchConfig(): Promise<ConfigPayload> {
  return request<ConfigPayload>(
    '/v1/config',
    { method: 'GET', headers: adminHeaders() },
    20_000,
  )
}

/** Validate, persist and hot-reload one batch of `.env` changes. */
export async function updateConfig(
  values: Record<string, string>,
): Promise<ConfigUpdateResult> {
  return request<ConfigUpdateResult>(
    '/v1/config',
    { method: 'PUT', headers: adminHeaders(), body: JSON.stringify({ values }) },
    30_000,
  )
}
