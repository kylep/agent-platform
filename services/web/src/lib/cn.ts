import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// The shadcn convention: clsx for conditional classes, tailwind-merge so a
// caller's className can override a primitive's defaults without !important.
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
