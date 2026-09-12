import { Link } from "react-router-dom";
import { cn } from "./cn";

// The dashboard/reporting stat card. `sub` is the small print under the label
// — the breakdown behind the number, or a gauge that qualifies it.
export function Stat({ label, value, warn, to, sub }: {
  label: string; value: string | number; warn?: boolean; to?: string;
  sub?: React.ReactNode;
}) {
  // A card with small print under it is wider than the rest; letting it grow
  // into whatever row it lands on beats a lone tile with dead space beside it.
  const fill = sub && "grow basis-64";
  const inner = (
    <div className={cn("h-full min-w-28 rounded-lg border border-border bg-surface px-4 py-3",
                       warn && "border-warning", !to && fill)}>
      <div className={cn("text-xl font-semibold", warn ? "text-warning" : "text-default")}>{value}</div>
      <div className="mt-0.5 text-[11px] uppercase tracking-wider text-subtle">{label}</div>
      {sub && <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-muted">{sub}</div>}
    </div>
  );
  return to ? <Link to={to} className={cn("no-underline", fill)}>{inner}</Link> : inner;
}

export function StatRow({ children }: { children: React.ReactNode }) {
  return <div className="mb-4 flex flex-wrap gap-3">{children}</div>;
}
