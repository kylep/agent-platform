import { useEffect, useRef, useState } from "react";
import type { HTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from "react";
import { cn } from "./cn";
import "./table.css";

// Which edge still has content behind it, if any — the fade in table.css keys
// off this. "" means the table fits and nothing is faded.
type Overflow = "" | "start" | "end" | "both";

// The console's data table. Compose <Table><thead>… — TH/TD carry the cell
// styling so markup stays plain HTML where that reads better.
export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  // The scroll container keeps a wide table from pushing the whole page
  // sideways — overflow stays inside the table, never on the document. What it
  // can't do on its own is say so: an overlay scrollbar is invisible at rest,
  // so a column past the edge reads as clipped. Watching the scroll position
  // marks the edge that has more behind it (see table.css).
  const ref = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState<Overflow>("");
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => {
      const max = el.scrollWidth - el.clientWidth;
      // Sub-pixel layout leaves a fraction of slack on tables that do fit.
      if (max <= 1) { setOverflow(""); return; }
      const atStart = el.scrollLeft <= 1;
      const atEnd = el.scrollLeft >= max - 1;
      setOverflow(atStart ? "end" : atEnd ? "start" : "both");
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    // Rows loading in change the table's width without resizing the container.
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => { el.removeEventListener("scroll", update); ro.disconnect(); };
  }, []);
  return (
    <div ref={ref} className="ui-table-scroll overflow-x-auto" data-overflow={overflow}>
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
