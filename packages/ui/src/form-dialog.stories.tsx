import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { Button } from "./button";
import { FormDialog } from "./dialog";
import { Input } from "./field";

const meta: Meta<typeof FormDialog> = { title: "UI/FormDialog", component: FormDialog };
export default meta;

function RenameAgent({ initiallyOpen = false }: { initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  const [name, setName] = useState("");
  const [savedName, setSavedName] = useState("");

  return (
    <>
      <Button onClick={() => setOpen(true)}>Rename agent</Button>
      <p role="status">{savedName ? `Saved name: ${savedName}` : ""}</p>
      <FormDialog open={open} title="Rename agent" submitLabel="Save name"
                  disabled={!name.trim()} onCancel={() => setOpen(false)}
                  onSubmit={() => {
                    setSavedName(name.trim());
                    setOpen(false);
                  }}>
        <label className="flex flex-col gap-1">
          Agent name
          <Input value={name} onChange={(event) => setName(event.target.value)} />
        </label>
      </FormDialog>
    </>
  );
}

export const Rename: StoryObj = {
  render: () => <RenameAgent />,
};

export const EmptyDraft: StoryObj = {
  render: () => <RenameAgent initiallyOpen />,
};
