import { useMemo, useState } from 'react'

type Val = number | string | null | undefined
export interface SortState { key: string; dir: 1 | -1 }

/** Click-to-sort column headers. Missing values always go last, whichever way you sort. `initial` = null keeps the
 *  order the rows arrived in until a header is clicked. */
export function useSort<T>(rows: T[], get: Record<string, (r: T) => Val>, initial: SortState | null = null) {
  const [sort, setSort] = useState<SortState | null>(initial)
  const sorted = useMemo(() => {
    if (!sort || !get[sort.key]) return rows
    const f = get[sort.key]
    return [...rows].sort((a, b) => {
      const x = f(a), y = f(b)
      const nx = x == null || x === '', ny = y == null || y === ''
      if (nx || ny) return nx === ny ? 0 : nx ? 1 : -1
      return (x! < y! ? -1 : x! > y! ? 1 : 0) * sort.dir
    })
  }, [rows, sort, get])
  const th = (key: string, label: string, cls = '', text = false) => (
    <th className={cls} aria-sort={sort?.key === key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
      <button className="uppercase hover:text-yellow" onClick={() => setSort(s => ({ key, dir: s?.key === key ? (s.dir === 1 ? -1 : 1) : (text ? 1 : -1) }))}>
        {label}{sort?.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
      </button>
    </th>
  )
  return { rows: sorted, th, sort, reset: () => setSort(initial) }
}
