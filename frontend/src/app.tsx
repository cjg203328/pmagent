import { useEffect } from 'react'

import { Sidebar } from '@/components/sidebar'
import { ChatPage } from '@/pages/chat'
import { ObservabilityPage } from '@/pages/observability'
import { SettingsPage } from '@/pages/settings'
import { useAppStore } from '@/store/app-store'

export function App() {
  const view = useAppStore((state) => state.view)
  const loadConversations = useAppStore((state) => state.loadConversations)
  const loadCapabilities = useAppStore((state) => state.loadCapabilities)

  useEffect(() => {
    void loadConversations()
    void loadCapabilities()
  }, [loadConversations, loadCapabilities])

  return (
    <div className="flex h-full min-h-0 overflow-hidden bg-canvas">
      <Sidebar />
      <main className="min-w-0 flex-1">
        {view === 'chat' ? <ChatPage /> : null}
        {view === 'settings' ? <SettingsPage /> : null}
        {view === 'observability' ? <ObservabilityPage /> : null}
      </main>
    </div>
  )
}
