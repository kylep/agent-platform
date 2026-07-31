import type { HTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from "react";
import { cn } from "../lib/cn";

// The console's data table. Compose <Table><thead>… — TH/TD carry the cell
// styling so markup stays plain HTML where that reads better.
export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  // The scroll container keeps a wide table from pushing the whole page
  // sideways — overflow stays inside the table, never on the document.
  return (
    <div className="overflow-x-auto">
      <table className={cn("w-full border-collapse text-sm [&_tbody_tr]:border-t [&_tbody_tr]:border-border", className)}
             {...props} />
    </div>
  );
}

export function TH({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th className={cn("px-2 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wider text-subtle", className)}
        {...props} />
  );
}

export function TD({ className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-2 py-1.5 align-top", className)} {...props} />;
}
