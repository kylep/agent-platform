import { Link } from "react-router-dom";
import { cn } from "../lib/cn";

// The dashboard/reporting stat card.
export function Stat({ label, value, warn, to }: {
  label: string; value: string | number; warn?: boolean; to?: string;
}) {
  const inner = (
    <div className={cn("min-w-28 rounded-lg border border-border bg-surface px-4 py-3",
                       warn && "border-warning")}>
      <div className={cn("text-xl font-semibold", warn ? "text-warning" : "text-default")}>{value}</div>
      <div className="mt-0.5 text-[11px] uppercase tracking-wider text-subtle">{label}</div>
    </div>
  );
  return to ? <Link to={to} className="no-underline">{inner}</Link> : inner;
}

export function StatRow({ children }: { children: React.ReactNode }) {
  return <div className="mb-4 flex flex-wrap gap-3">{children}</div>;
}
