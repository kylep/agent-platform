import type { Meta, StoryObj } from "@storybook/react-vite";
import { CodeEditor, Input, Select, Textarea } from "./field";

const meta: Meta = { title: "UI/Field" };
export default meta;

export const AllFields: StoryObj = {
  render: () => (
    <div className="form-col" style={{ maxWidth: 480 }}>
      <Input placeholder="secret name (e.g. discord-bot)" aria-label="Name" />
      <Select aria-label="Filter by agent">
        <option>All agents</option>
        <option>health-monitor</option>
      </Select>
      <Textarea rows={2} placeholder="Describe a change in plain language…" aria-label="Instruction" />
      <CodeEditor rows={6} aria-label="Agent definition"
                  defaultValue={"---\nname: news\ntools: WebSearch, WebFetch\n---\nYou gather the day's notable news."} />
    </div>
  ),
};
