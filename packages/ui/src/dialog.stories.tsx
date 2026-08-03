import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { Button } from "./button";
import { ConfirmDialog } from "./dialog";

const meta: Meta = { title: "UI/ConfirmDialog" };
export default meta;

export const Discard: StoryObj = {
  render: function Render() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <Button variant="secondary" onClick={() => setOpen(true)}>Discard</Button>
        <ConfirmDialog open={open} title="Discard change #12?"
                       confirmLabel="Discard it"
                       onConfirm={() => setOpen(false)} onCancel={() => setOpen(false)}>
          "Edit news: agent definition" will be closed and its branch deleted. This can't be
          undone from here.
        </ConfirmDialog>
      </>
    );
  },
};
