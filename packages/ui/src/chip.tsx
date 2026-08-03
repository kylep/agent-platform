import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes, HTMLAttributes } from "react";
import { cn } from "./cn";

// The platform's status vocabulary: small uppercase pills. Statuses map 1:1
// to semantic tokens; `status()` converts an API status string to a variant.
export const chipVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 " +
  "text-[11px] font-medium uppercase tracking-wider whitespace-nowrap",
  {
    variants: {
      variant: {
        neutral: "border-border text-muted",
        ok: "border-success text-success",
        warn: "border-warning text-warning",
        danger: "border-danger text-danger",
        accent: "border-accent text-accent",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

export function chipStatusVariant(status: string): VariantProps<typeof chipVariants>["variant"] {
  if (["valid", "ok", "succeeded", "up", "live", "configured", "working", "enabled"].includes(status)) return "ok";
  if (["unprobed", "pending", "running", "queued", "dispatched", "deploying"].includes(status)) return "warn";
  if (["invalid", "missing", "failed", "rejected", "dlq", "killed", "timeout", "down", "quarantined", "blocked"].includes(status)) return "danger";
  return "neutral";
}

type ChipProps = HTMLAttributes<HTMLSpanElement> & VariantProps<typeof chipVariants>;

export function Chip({ className, variant, ...props }: ChipProps) {
  return <span className={cn(chipVariants({ variant }), className)} {...props} />;
}

// A clickable chip (filters, legend toggles) — same look, real button.
type ChipButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof chipVariants>;

export function ChipButton({ className, variant, type = "button", ...props }: ChipButtonProps) {
  return (
    <button type={type}
            className={cn(chipVariants({ variant }), "cursor-pointer appearance-none bg-transparent font-sans",
                          "focus-visible:outline-2 focus-visible:outline-accent", className)}
            {...props} />
  );
}

export function StatusChip({ status, className }: { status: string; className?: string }) {
  return <Chip variant={chipStatusVariant(status)} className={className}>{status}</Chip>;
}
