/** 左右侧边栏拖拽缩放：只在拖拽期间挂全局监听。 */
export function beginResize(
  edge: 'left' | 'right',
  event: PointerEvent,
  apply: (width: number) => void,
): void {
  if (event.button !== 0) return
  event.preventDefault()
  const root = document.documentElement
  root.classList.add('resizing')

  const move = (moveEvent: PointerEvent) => {
    const width = edge === 'left'
      ? moveEvent.clientX
      : window.innerWidth - moveEvent.clientX
    apply(width)
  }

  const stop = () => {
    root.classList.remove('resizing')
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', stop)
    window.removeEventListener('pointercancel', stop)
  }

  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', stop)
  window.addEventListener('pointercancel', stop)
}
