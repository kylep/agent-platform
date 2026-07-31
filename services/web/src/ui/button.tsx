import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "../lib/cn";

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 rounded-md font-medium " +
  "cursor-pointer transition-colors disabled:cursor-not-allowed disabled:opacity-50 " +
  "focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2",
  {
    variants: {
      variant: {
        primary: "bg-accent text-on-accent hover:bg-accent-hover",
        secondary: "border border-border bg-transparent text-default hover:border-subtle",
        danger: "bg-danger text-on-accent hover:opacity-90",
        // an inline, link-shaped action (the old .linkish)
        link: "bg-transparent p-0 text-link underline-offset-2 hover:underline",
      },
      size: {
        md: "px-3 py-1.5 text-sm",
        sm: "px-2 py-1 text-xs",
        bare: "",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants>;

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button({ className, variant, size, type = "button", ...props }, ref) {
    return (
      <button ref={ref} type={type}
              className={cn(buttonVariants({ variant, size: variant === "link" ? "bare" : size }), className)}
              {...props} />
    );
  },
);
