import { useCallback, useRef } from 'react'

/** Makes a horizontally overflowing strip usable with a plain mouse: the wheel scrolls it sideways
 *  (only while it can still scroll that way, so the page keeps scrolling at the ends) and the strip
 *  can be dragged. A drag never counts as a click on the buttons inside it.
 *  Returns a callback ref, so it attaches whenever the element mounts. */
export function useHScroll<T extends HTMLElement>() {
  const cleanup = useRef<(() => void) | null>(null)
  return useCallback((el: T | null) => {
    cleanup.current?.()
    cleanup.current = null
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return      // zoom, or already horizontal (trackpad)
      const max = el.scrollWidth - el.clientWidth
      if (max <= 0) return
      const next = Math.max(0, Math.min(max, el.scrollLeft + e.deltaY))
      if (next === el.scrollLeft) return                                    // at an end: let the page scroll
      e.preventDefault()
      el.scrollLeft = next
    }
    let startX = 0, startLeft = 0, dragging = false, moved = false
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0 || e.pointerType !== 'mouse') return
      startX = e.clientX; startLeft = el.scrollLeft; dragging = true; moved = false
    }
    const onMove = (e: PointerEvent) => {
      if (!dragging) return
      const dx = e.clientX - startX
      if (!moved && Math.abs(dx) > 4) { moved = true; el.style.cursor = 'grabbing'; el.style.userSelect = 'none' }
      if (moved) el.scrollLeft = startLeft - dx
    }
    const onUp = () => {
      if (!dragging) return
      dragging = false
      el.style.cursor = ''; el.style.userSelect = ''
    }
    const onClickCapture = (e: MouseEvent) => {
      if (moved) { e.preventDefault(); e.stopPropagation(); moved = false }
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    el.addEventListener('pointerdown', onDown)
    el.addEventListener('click', onClickCapture, true)
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    cleanup.current = () => {
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('pointerdown', onDown)
      el.removeEventListener('click', onClickCapture, true)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [])
}
