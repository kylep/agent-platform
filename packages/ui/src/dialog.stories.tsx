import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { Button } from "./button";
import { ConfirmDialog } from "./dialog";

const meta: Meta = { title: "UI/ConfirmDialog" };
export default meta;

function DiscardExample({ initiallyOpen = false }: { initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  const [discarded, setDiscarded] = useState(false);
  return (
    <>
      <Button variant="secondary" onClick={() => { setDiscarded(false); setOpen(true); }}>
        Discard
      </Button>
      <p role="status">{discarded ? "Change discarded." : ""}</p>
      <ConfirmDialog open={open} title="Discard change #12?"
                     confirmLabel="Discard it"
                     onConfirm={() => { setDiscarded(true); setOpen(false); }}
                     onCancel={() => setOpen(false)}>
        "Edit news: agent definition" will be closed and its branch deleted. This can't be
        undone from here.
      </ConfirmDialog>
    </>
  );
}

export const Discard: StoryObj = {
  render: () => <DiscardExample />,
};

export const Open: StoryObj = {
  render: () => <DiscardExample initiallyOpen />,
};
