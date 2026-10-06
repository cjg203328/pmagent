/** Gateway response types — mirror artpm_agent/api models. */

export interface Conversation {
  id: string
  title: string
  workspace_id?: string
  created_at?: string
  updated_at?: string
}

export interface Message {
  id?: number
  role: 'user' | 'assistant' | 'system' | string
  content: string
  status?: string
  created_at?: string
  turn_id?: string
}

export interface Capability {
  name: string
  description?: string
  [key: string]: unknown
}

export interface ChatResponse {
  conversation_id: string
  turn_id: string
  response: string
  success: boolean
  awaiting_approval?: boolean
  permission_request_id?: string | null
  handled_by?: string
  error?: { code: string; message: string; request_id?: string }
}

export interface ReadinessPayload {
  status?: string
  ready?: boolean
  service?: string
  version?: string
  checks?: Record<string, { status?: string }>
}

export interface GatewayErrorShape {
  error?: { code?: string; message?: string }
}

export type ConnectionState = 'checking' | 'online' | 'offline'

/**
 * Conversation-scoped approval policy, mirroring
 * artpm_agent/security/access_mode.py. The model can never choose this value;
 * it is persisted per conversation and read by the server's approval gate.
 */
export type AccessMode = 'read_only' | 'controlled' | 'full_access'

export interface AccessModeGrant {
  conversation_id?: string
  mode?: AccessMode
  issued_at?: string
  expires_at?: string
}

/** Field kinds the gateway's config schema can emit. */
export type ConfigFieldKind = 'text' | 'secret' | 'bool' | 'int' | 'float' | 'select'

export interface ConfigFieldPayload {
  key: string
  label: string
  kind: ConfigFieldKind
  help: string
  options: string[]
  placeholder: string
  /**
   * For `secret` fields this is always empty. Use `configured` plus `preview`
   * to render state; the plaintext never reaches the browser.
   */
  value: string
  preview?: string
  configured: boolean
}

export interface ConfigGroupPayload {
  id: string
  title: string
  description: string
  fields: ConfigFieldPayload[]
}

export interface ConfigPayload {
  env_path: string
  groups: ConfigGroupPayload[]
  provider: string
  model: string
}

export interface ConfigUpdateResult {
  applied: string[]
  env_path: string
  hot_reloaded: boolean
  restart_required: boolean
}
