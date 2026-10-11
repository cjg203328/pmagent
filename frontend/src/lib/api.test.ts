/**
 * API-layer contract tests.
 *
 * These pin the request shape and the error translation the store relies on.
 * `request()` calls `window.setTimeout`, so a minimal `window` stub is installed
 * instead of pulling in a full DOM environment.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ACTOR_ID,
  ApiError,
  TENANT_ID,
  WORKSPACE_ID,
  deleteConversation,
  fetchAccessMode,
  fetchCapabilities,
  fetchConfig,
  fetchConversations,
  fetchMessages,
  sendChat,
  setAccessMode,
  setConversationArchived,
  updateConfig,
} from './api'

/** Real timers keep `window.setTimeout`/`clearTimeout` faithful. */
const windowStub = {
  setTimeout: globalThis.setTimeout,
  clearTimeout: globalThis.clearTimeout,
}

vi.stubGlobal('window', windowStub)

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.stubGlobal('window', windowStub)
})

/** The `fetch` call recorded by the mock, with its parsed body. */
function lastCall() {
  const [url, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit]
  return {
    url,
    init,
    headers: init.headers as Record<string, string>,
    body: init.body ? JSON.parse(init.body as string) : undefined,
  }
}

describe('request plumbing', () => {
  it('prefixes the gateway base path and attaches the identity headers', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ items: [] }))

    await fetchConversations()

    const { url, headers } = lastCall()
    expect(url).toBe('/api/v1/conversations')
    expect(headers['x-workspace-id']).toBe(WORKSPACE_ID)
    expect(headers['x-actor-id']).toBe(ACTOR_ID)
    expect(headers['x-tenant-id']).toBe(TENANT_ID)
    expect(headers['x-actor-role']).toBe('user')
  })

  it('translates a gateway error envelope into an ApiError with its code', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { code: 'forbidden', message: '无权访问。' } },
        { status: 403 },
      ),
    )

    await expect(fetchConversations()).rejects.toMatchObject({
      name: 'ApiError',
      code: 'forbidden',
      status: 403,
      message: '无权访问。',
    })
  })

  it('falls back to an HTTP-derived code when the envelope is missing', async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, { status: 500 }))

    await expect(fetchConversations()).rejects.toMatchObject({
      code: 'http_500',
    })
  })

  it('reports a transport failure as network_error rather than leaking it', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    const error = await fetchConversations().catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('network_error')
  })
})

describe('conversation endpoints', () => {
  it('requests the archived bucket when asked', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ items: [] }))

    await fetchConversations(true)

    expect(lastCall().url).toBe('/api/v1/conversations?archived=true')
  })

  it('returns an empty list when the gateway omits items', async () => {
    fetchMock.mockResolvedValue(jsonResponse({}))

    await expect(fetchCapabilities()).resolves.toEqual([])
  })

  it('encodes the conversation id into the path', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ items: [] }))

    await fetchMessages('a/b c')

    expect(lastCall().url).toBe('/api/v1/conversations/a%2Fb%20c/messages')
  })

  it('omits conversation_id on the first turn and includes it afterwards', async () => {
    // A Response body is a one-shot stream, so each call needs a fresh object.
    fetchMock.mockImplementation(async () =>
      jsonResponse({
        conversation_id: 'conv-a',
        turn_id: 't1',
        response: 'ok',
        success: true,
      }),
    )

    await sendChat('hi')
    expect(lastCall().body).toEqual({ message: 'hi' })

    await sendChat('again', 'conv-a')
    expect(lastCall().body).toEqual({ message: 'again', conversation_id: 'conv-a' })
  })

  it('archives with PATCH and sends the flag', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ item: { id: 'conv-a', title: 'A' } }))

    const item = await setConversationArchived('conv-a', true)

    const { url, init, body } = lastCall()
    expect(url).toBe('/api/v1/conversations/conv-a')
    expect(init.method).toBe('PATCH')
    expect(body).toEqual({ archived: true })
    expect(item.id).toBe('conv-a')
  })

  it('deletes with DELETE and tolerates an empty body', async () => {
    // 204 must carry no body at all, not even an empty string.
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))

    await expect(deleteConversation('conv-a')).resolves.toBeUndefined()
    expect(lastCall().init.method).toBe('DELETE')
  })
})

describe('access mode endpoints', () => {
  it('defaults a missing grant to the controlled baseline', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ conversation_id: 'conv-a' }))

    await expect(fetchAccessMode('conv-a')).resolves.toBe('controlled')
  })

  it('sends the chosen mode with PUT and echoes the server value', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ mode: 'read_only' }))

    const applied = await setAccessMode('conv-a', 'read_only')

    const { url, init, body } = lastCall()
    expect(url).toBe('/api/v1/conversations/conv-a/access-mode')
    expect(init.method).toBe('PUT')
    expect(body).toEqual({ mode: 'read_only' })
    expect(applied).toBe('read_only')
  })
})

describe('config endpoints', () => {
  it('requests the admin role for reads', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ env_path: '.env', groups: [], provider: 'p', model: 'm' }),
    )

    await fetchConfig()

    const { url, init, headers } = lastCall()
    expect(url).toBe('/api/v1/config')
    expect(init.method).toBe('GET')
    expect(headers['x-actor-role']).toBe('admin')
  })

  it('wraps the edited fields in a values envelope for writes', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        applied: ['ARTPM_MODEL'],
        env_path: '.env',
        hot_reloaded: true,
        restart_required: false,
      }),
    )

    const result = await updateConfig({ ARTPM_MODEL: 'deepseek-reasoner' })

    const { init, headers, body } = lastCall()
    expect(init.method).toBe('PUT')
    expect(headers['x-actor-role']).toBe('admin')
    expect(body).toEqual({ values: { ARTPM_MODEL: 'deepseek-reasoner' } })
    expect(result.applied).toEqual(['ARTPM_MODEL'])
  })
})
