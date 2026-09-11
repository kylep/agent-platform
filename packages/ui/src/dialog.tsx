import * as RadixDialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { Button } from "./button";

// The modal shell — Radix underneath (focus trap, esc, aria) with the
// platform's look. Both dialogs below are the same box; what differs is what
// the buttons do.
function Shell({ open, title, children, actions, onCancel }: {
  open: boolean; title: string; children: ReactNode;
  actions: ReactNode; onCancel: () => void;
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
          <div className="flex gap-2">{actions}</div>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

// One shape fits every "one-way door" confirm in the app.
export function ConfirmDialog({ open, title, children, confirmLabel, onConfirm, onCancel }: {
  open: boolean; title: string; children: ReactNode;
  confirmLabel: string; onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <Shell open={open} title={title} onCancel={onCancel} actions={
      <>
        <Button variant="danger" onClick={onConfirm}>{confirmLabel}</Button>
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
      </>
    }>{children}</Shell>
  );
}

// The same box asking for a value instead of a yes: a couple of fields and a
// primary action. `children` are the fields; the dialog owns nothing about
// them, so every caller keeps its own draft state.
export function FormDialog({ open, title, children, submitLabel, disabled, onSubmit, onCancel }: {
  open: boolean; title: string; children: ReactNode;
  submitLabel: string; disabled?: boolean;
  onSubmit: () => void; onCancel: () => void;
}) {
  return (
    <Shell open={open} title={title} onCancel={onCancel} actions={
      <>
        <Button onClick={onSubmit} disabled={disabled}>{submitLabel}</Button>
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
      </>
    }>{children}</Shell>
  );
}
