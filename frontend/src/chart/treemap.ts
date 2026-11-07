/** Squarified treemap (Bruls, Huizing, van Wijk): lays items out in a rectangle so each area is proportional to its
 *  value and the tiles stay close to square. Pure geometry, no drawing. */
export interface Rect { x: number; y: number; w: number; h: number }
export interface Placed<T> { item: T; value: number; rect: Rect }

/** The worst aspect ratio in a row of `areas` laid along a side of length `side`. */
function worst(areas: number[], side: number): number {
  const s = areas.reduce((a, b) => a + b, 0)
  const mx = Math.max(...areas), mn = Math.min(...areas)
  return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn))
}

export function squarify<T>(items: { item: T; value: number }[], bounds: Rect): Placed<T>[] {
  const nodes = items.filter(i => i.value > 0 && Number.isFinite(i.value)).sort((a, b) => b.value - a.value)
  if (!nodes.length || bounds.w <= 0 || bounds.h <= 0) return []
  const total = nodes.reduce((a, n) => a + n.value, 0)
  const scale = (bounds.w * bounds.h) / total
  const areas = nodes.map(n => n.value * scale)
  const out: Placed<T>[] = []
  let { x, y, w, h } = bounds
  let i = 0
  while (i < nodes.length) {
    const side = Math.min(w, h)
    let row = [areas[i]]
    let j = i + 1
    let best = worst(row, side)
    while (j < nodes.length) {
      const next = [...row, areas[j]]
      const wn = worst(next, side)
      if (wn > best) break
      row = next; best = wn; j++
    }
    const rowSum = row.reduce((a, b) => a + b, 0)
    const thick = rowSum / side
    if (w >= h) {                       // a column on the left, tiles stacked down it
      let cy = y
      row.forEach((a, k) => { const hh = a / thick; out.push({ item: nodes[i + k].item, value: nodes[i + k].value, rect: { x, y: cy, w: thick, h: hh } }); cy += hh })
      x += thick; w -= thick
    } else {                            // a row along the top, tiles side by side
      let cx = x
      row.forEach((a, k) => { const ww = a / thick; out.push({ item: nodes[i + k].item, value: nodes[i + k].value, rect: { x: cx, y, w: ww, h: thick } }); cx += ww })
      y += thick; h -= thick
    }
    i = j
  }
  return out
}

export interface Group<T> { key: string; items: { item: T; value: number }[] }
export interface PlacedGroup<T> { key: string; value: number; rect: Rect; head: number; tiles: Placed<T>[] }

/** Two levels: groups (sectors) laid out first, then each group's items inside it, below a header of `head` px. A group too
 *  small to hold a header and a few pixels of tiles gets no header (`head` 0) so its items still show. */
export function squarify2<T>(groups: Group<T>[], bounds: Rect, head = 16, pad = 1): PlacedGroup<T>[] {
  const placed = squarify(groups.map(g => ({ item: g, value: g.items.reduce((a, b) => a + Math.max(b.value, 0), 0) })), bounds)
  return placed.map(p => {
    const r = p.rect
    const hd = r.h >= head + 10 && r.w >= 40 ? head : 0
    const pd = Math.min(pad, r.w / 4, r.h / 4)                      // never let the padding eat a sliver of a group
    const inner: Rect = { x: r.x + pd, y: r.y + hd, w: Math.max(0, r.w - 2 * pd), h: Math.max(0, r.h - hd - pd) }
    return { key: p.item.key, value: p.value, rect: r, head: hd, tiles: squarify(p.item.items, inner) }
  })
}
