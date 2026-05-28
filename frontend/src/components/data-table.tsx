interface DataTableProps {
  columns: string[];
  rows: Record<string, unknown>[];
}

export function DataTable({ columns, rows }: DataTableProps) {
  if (columns.length === 0 || rows.length === 0) return null;

  return (
    <div className="mt-2 border rounded-lg overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-muted/50 border-b">
            {columns.map((col) => (
              <th key={col} className="px-3 py-2 text-left font-medium text-muted-foreground whitespace-nowrap">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b last:border-0 hover:bg-muted/20 transition-colors">
              {columns.map((col) => (
                <td key={col} className="px-3 py-2 whitespace-nowrap">
                  {formatCell(row[col])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    // Format currency-like numbers
    if (value > 1000) {
      return new Intl.NumberFormat("id-ID").format(value);
    }
    return String(value);
  }
  return String(value);
}
