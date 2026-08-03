import * as RadixDialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { Button } from "./button";

// The confirmation modal — Radix underneath (focus trap, esc, aria) with the
// platform's look. One shape fits every "one-way door" confirm in the app.
export function ConfirmDialog({ open, title, children, confirmLabel, onConfirm, onCancel }: {
  open: boolean; title: string; children: ReactNode;
  confirmLabel: string; onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <RadixDialog.Root open={open} onOpenChange={(o) => { if (!o) onCancel(); }}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <RadixDialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-[28rem] max-w-[92vw] -translate-x-1/2 -translate-y-1/2
                     rounded-lg border border-border bg-raised p-5 shadow-xl">
          <RadixDialog.Title className="mb-2 text-lg font-semibold text-default">{title}</RadixDialog.Title>
          <RadixDialog.Description asChild>
            <div className="mb-4 text-sm text-muted">{children}</div>
          </RadixDialog.Description>
          <div className="flex gap-2">
            <Button variant="danger" onClick={onConfirm}>{confirmLabel}</Button>
            <Button variant="secondary" onClick={onCancel}>Cancel</Button>
          </div>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
