export interface DesktopInfo {
  name: string
  version: string
  packaged: boolean
}

export interface DesktopBridge {
  getInfo(): Promise<DesktopInfo>
  minimize(): Promise<{ ok: boolean }>
  toggleMaximize(): Promise<{ ok: boolean; maximized: boolean }>
  close(): Promise<{ ok: boolean }>
  isMaximized(): Promise<{ ok: boolean; maximized: boolean }>
  onMaximized(callback: (maximized: boolean) => void): () => void
}

declare global {
  interface Window {
    alembic?: DesktopBridge
  }
}
