import { forwardRef, type InputHTMLAttributes, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "./cn";

const fieldBase =
  "rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm text-default " +
  "placeholder:text-subtle focus-visible:outline-2 focus-visible:outline-accent " +
  "disabled:opacity-50 read-only:opacity-70";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return <input ref={ref} className={cn(fieldBase, className)} {...props} />;
  },
);

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    return <textarea ref={ref} className={cn(fieldBase, "w-full resize-y", className)} {...props} />;
  },
);

// The mono raw-file editor (agent.md / SKILL.md / secret.yaml / entrypoints).
export const CodeEditor = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function CodeEditor({ className, ...props }, ref) {
    return (
      <textarea ref={ref} spellCheck={false}
                className={cn(fieldBase, "w-full resize-y font-mono text-[13px] leading-relaxed", className)}
                {...props} />
    );
  },
);

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, ...props }, ref) {
    return <select ref={ref} className={cn(fieldBase, "cursor-pointer", className)} {...props} />;
  },
);
