// Your own drawings: the data shape and the pure geometry (snapping, hit-testing, moving). No chart imports, so the
// unit tests run under plain Node; drawings.ts holds the chart primitive that paints them.

export type DrawKind = 'hline' | 'trend' | 'rect' | 'text'
export interface DrawPoint { time: string; price: number }
export interface Drawing { id: string; kind: DrawKind; p1: DrawPoint; p2?: DrawPoint; text?: string; color: string }
/** Two-click tools need a second point; the others are placed with one click. */
export const TWO_POINT: Record<DrawKind, boolean> = { hline: false, trend: true, rect: true, text: false }
export const DRAW_COLORS = ['#ffa028', '#5aa9ff', '#4af6c3', '#ff433d', '#f0f0f0']   // amber, blue, cyan, red, white
export const MAX_DRAWINGS = 200

let seq = 0
export const newId = () => `d${Date.now().toString(36)}${(seq++).toString(36)}`

// ---------------------------------------------------------------- pure geometry (tested)
export interface Px { x: number; y: number }

/** Distance from point p to the segment a-b, in pixels. */
export function segDist(p: Px, a: Px, b: Px): number {
  const dx = b.x - a.x, dy = b.y - a.y
  const len2 = dx * dx + dy * dy
  const t = len2 ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2)) : 0
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))
}

/** "Magnet": the bar's open, high, low or close nearest the pointer, if it is within `px` pixels; else the pointer's price. */
export function snapPrice(price: number, bar: { open: number; high: number; low: number; close: number } | undefined,
  toY: (p: number) => number | null, y: number, px = 8): number {
  if (!bar) return price
  let best = price, bestD = px
  for (const v of [bar.open, bar.high, bar.low, bar.close]) {
    const yy = toY(v)
    if (yy == null) continue
    const d = Math.abs(yy - y)
    if (d <= bestD) { best = v; bestD = d }
  }
  return best
}

/** Round a price to a sensible number of decimals for its size (no 21.534781 on a chart). */
export function tidy(price: number): number {
  const a = Math.abs(price)
  const nd = a >= 1000 ? 1 : a >= 10 ? 2 : a >= 1 ? 3 : 4
  return Math.round(price * 10 ** nd) / 10 ** nd
}

export interface Hit { id: string; part: 'body' | 'p1' | 'p2' }
export interface Placed { d: Drawing; a: Px; b: Px | null; w: number }   // pixel positions (w = text width)

/** Which drawing (and which part of it) is under the pointer. Handles win over bodies; the newest drawing wins. */
export function hitTest(placed: Placed[], p: Px, tol = 6): Hit | null {
  for (let i = placed.length - 1; i >= 0; i--) {
    const { d, a, b } = placed[i]
    if (b && Math.hypot(p.x - b.x, p.y - b.y) <= tol + 2) return { id: d.id, part: 'p2' }
    if (d.kind !== 'hline' && Math.hypot(p.x - a.x, p.y - a.y) <= tol + 2) return { id: d.id, part: 'p1' }
  }
  for (let i = placed.length - 1; i >= 0; i--) {
    const { d, a, b, w } = placed[i]
    if (d.kind === 'hline' && Math.abs(p.y - a.y) <= tol) return { id: d.id, part: 'body' }
    if (d.kind === 'trend' && b && segDist(p, a, b) <= tol) return { id: d.id, part: 'body' }
    if (d.kind === 'rect' && b) {
      const x1 = Math.min(a.x, b.x), x2 = Math.max(a.x, b.x), y1 = Math.min(a.y, b.y), y2 = Math.max(a.y, b.y)
      if (p.x >= x1 - tol && p.x <= x2 + tol && p.y >= y1 - tol && p.y <= y2 + tol) return { id: d.id, part: 'body' }
    }
    if (d.kind === 'text' && p.x >= a.x - 4 && p.x <= a.x + w + 4 && p.y >= a.y - 16 && p.y <= a.y + 4) return { id: d.id, part: 'body' }
  }
  return null
}

/** Move a whole drawing by a number of bars and a price difference (bar labels come from `labelAt`). */
export function shifted(d: Drawing, dBars: number, dPrice: number, indexOf: (t: string) => number | null, labelAt: (i: number) => string | null): Drawing {
  const mv = (p: DrawPoint): DrawPoint => {
    const i = indexOf(p.time)
    const t = i == null ? null : labelAt(i + dBars)
    return { time: t ?? p.time, price: tidy(p.price + dPrice) }
  }
  return { ...d, p1: mv(d.p1), p2: d.p2 ? mv(d.p2) : undefined }
}

