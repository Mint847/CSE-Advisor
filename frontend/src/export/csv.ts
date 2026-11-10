// CSV export for tables (positions, candidates, All stocks, screens, backtest trades...). Plain functions so they can be tested.
// Cells follow RFC 4180 (quote when a cell has a comma, quote or line break; double the quotes). A text cell that starts with = + - @
// would be run as a formula by Excel / Sheets, so it gets a leading apostrophe; real numbers are written as numbers.

export type CsvValue = string | number | boolean | null | undefined
export interface CsvColumn<T> { header: string; get: (row: T) => CsvValue }

const FORMULA = /^[=+\-@\t\r]/

export function csvCell(v: CsvValue): string {
  if (v == null) return ''
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : ''
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  let s = v
  if (FORMULA.test(s) && Number.isNaN(Number(s))) s = `'${s}`
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

export function toCsv<T>(columns: CsvColumn<T>[], rows: T[]): string {
  const lines = [columns.map(c => csvCell(c.header)).join(',')]
  for (const r of rows) lines.push(columns.map(c => csvCell(c.get(r))).join(','))
  return lines.join('\r\n') + '\r\n'
}

/** A file name like cse-advisor-positions-2026-11-10.csv (letters, digits and dashes only). */
export function csvFileName(name: string, d: Date = new Date()): string {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'export'
  return `cse-advisor-${slug}-${d.toISOString().slice(0, 10)}.csv`
}

/** Saves the CSV in the browser (with a byte-order mark so Excel reads the characters correctly). */
export function downloadCsv(name: string, csv: string): void {
  const url = URL.createObjectURL(new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = csvFileName(name)
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
