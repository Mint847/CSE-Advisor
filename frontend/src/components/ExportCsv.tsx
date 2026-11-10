// "Export CSV" button: saves the rows currently shown as a spreadsheet file.
import { downloadCsv, toCsv, type CsvColumn } from '../export/csv'

export default function ExportCsv<T>({ name, columns, rows, className = '' }: { name: string; columns: CsvColumn<T>[]; rows: T[]; className?: string }) {
  return (
    <button type="button" className={`btn !py-0.5 text-[11px] ${className}`} disabled={rows.length === 0} title={`Save these ${rows.length} rows as a CSV file (opens in Excel or Sheets)`}
            onClick={() => downloadCsv(name, toCsv(columns, rows))}>Export CSV</button>
  )
}
