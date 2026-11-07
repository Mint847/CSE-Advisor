import assert from 'node:assert/strict'
import { test } from 'node:test'
import { squarify, squarify2 } from './treemap.ts'

const area = (r: { w: number; h: number }) => r.w * r.h
const near = (a: number, b: number, eps = 1e-6) => Math.abs(a - b) < eps

test('areas are proportional to values and fill the rectangle', () => {
  const out = squarify([{ item: 'a', value: 6 }, { item: 'b', value: 3 }, { item: 'c', value: 2 }, { item: 'd', value: 1 }], { x: 0, y: 0, w: 120, h: 60 })
  assert.equal(out.length, 4)
  assert.ok(near(out.reduce((s, p) => s + area(p.rect), 0), 120 * 60))
  for (const p of out) assert.ok(near(area(p.rect) / (120 * 60), p.value / 12))
})

test('tiles stay inside the bounds and do not overlap', () => {
  const vals = [50, 30, 20, 15, 10, 8, 5, 3, 2, 1, 1, 0.5]
  const b = { x: 10, y: 20, w: 300, h: 180 }
  const out = squarify(vals.map((v, i) => ({ item: i, value: v })), b)
  for (const { rect: r } of out) {
    assert.ok(r.x >= b.x - 1e-6 && r.y >= b.y - 1e-6 && r.x + r.w <= b.x + b.w + 1e-6 && r.y + r.h <= b.y + b.h + 1e-6)
  }
  for (let i = 0; i < out.length; i++) for (let j = i + 1; j < out.length; j++) {
    const a = out[i].rect, c = out[j].rect
    const ox = Math.min(a.x + a.w, c.x + c.w) - Math.max(a.x, c.x), oy = Math.min(a.y + a.h, c.y + c.h) - Math.max(a.y, c.y)
    assert.ok(!(ox > 1e-6 && oy > 1e-6), `tiles ${i} and ${j} overlap`)
  }
})

test('the biggest is first and tiles are reasonably square', () => {
  const out = squarify(Array.from({ length: 20 }, (_, i) => ({ item: i, value: 20 - i })), { x: 0, y: 0, w: 400, h: 300 })
  assert.equal(out[0].item, 0)
  const worstRatio = Math.max(...out.map(p => Math.max(p.rect.w / p.rect.h, p.rect.h / p.rect.w)))
  assert.ok(worstRatio < 4, `worst aspect ratio ${worstRatio}`)
})

test('zero, negative and non-finite values and empty or collapsed bounds give nothing', () => {
  assert.deepEqual(squarify([{ item: 'a', value: 0 }, { item: 'b', value: -3 }, { item: 'c', value: NaN }], { x: 0, y: 0, w: 10, h: 10 }), [])
  assert.deepEqual(squarify([{ item: 'a', value: 1 }], { x: 0, y: 0, w: 0, h: 10 }), [])
  assert.equal(squarify([{ item: 'a', value: 5 }], { x: 0, y: 0, w: 10, h: 10 }).length, 1)
})

test('two levels: tiles sit inside their group below the header', () => {
  const g = squarify2([{ key: 'Finance', items: [{ item: 'x', value: 8 }, { item: 'y', value: 2 }] }, { key: 'Retail', items: [{ item: 'z', value: 5 }] }],
    { x: 0, y: 0, w: 200, h: 100 }, 16, 1)
  assert.deepEqual(g.map(x => x.key), ['Finance', 'Retail'])
  for (const grp of g) for (const t of grp.tiles) {
    assert.ok(t.rect.y >= grp.rect.y + 16 - 1e-6 && t.rect.x >= grp.rect.x + 1 - 1e-6 && t.rect.x + t.rect.w <= grp.rect.x + grp.rect.w + 1e-6 && t.rect.y + t.rect.h <= grp.rect.y + grp.rect.h + 1e-6)
  }
  assert.ok(near(g[0].rect.w * g[0].rect.h / (200 * 100), 10 / 15))
})

test('a group too small for a header still shows its items, inside its own box', () => {
  const g = squarify2([{ key: 'Big', items: [{ item: 'b', value: 1000 }] }, { key: 'Tiny', items: [{ item: 't', value: 1 }] }], { x: 0, y: 0, w: 300, h: 200 }, 16, 1)
  const tiny = g.find(x => x.key === 'Tiny')!
  assert.equal(tiny.head, 0)
  assert.equal(tiny.tiles.length, 1)
  const r = tiny.tiles[0].rect
  assert.ok(r.x >= tiny.rect.x - 1e-6 && r.y >= tiny.rect.y - 1e-6 && r.x + r.w <= tiny.rect.x + tiny.rect.w + 1e-6 && r.y + r.h <= tiny.rect.y + tiny.rect.h + 1e-6)
  assert.equal(g.find(x => x.key === 'Big')!.head, 16)
})
