import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "./cn";

// Inline callouts: info (default), success, danger. Replaces the legacy
// .banner / .banner-ok / .error trio.
export const bannerVariants = cva(
  "my-2 rounded-md border px-3 py-2 text-sm leading-relaxed",
  {
    variants: {
      variant: {
        info: "border-border bg-surface text-default",
        ok: "border-success/40 bg-surface text-success",
        danger: "border-danger/40 bg-surface text-danger",
      },
    },
    defaultVariants: { variant: "info" },
  },
);

type BannerProps = HTMLAttributes<HTMLDivElement> & VariantProps<typeof bannerVariants>;

export function Banner({ className, variant, ...props }: BannerProps) {
  return <div className={cn(bannerVariants({ variant }), className)} {...props} />;
}
