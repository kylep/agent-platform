import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { Button } from "./button";
import { InfoDialog } from "./dialog";

const meta: Meta<typeof InfoDialog> = { title: "UI/InfoDialog", component: InfoDialog };
export default meta;

function HelpExample({ initiallyOpen = false }: { initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <>
      <Button variant="secondary" onClick={() => setOpen(true)}>About run history</Button>
      <InfoDialog open={open} title="About run history" onClose={() => setOpen(false)}>
        Each row shows an agent run and its final status. Select a run ID to read its transcript.
      </InfoDialog>
    </>
  );
}

export const Help: StoryObj = {
  render: () => <HelpExample />,
};

export const Open: StoryObj = {
  render: () => <HelpExample initiallyOpen />,
};
