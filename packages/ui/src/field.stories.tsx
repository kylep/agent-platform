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

export const Disabled: StoryObj = {
  render: () => (
    <div className="form-col max-w-md">
      <Input aria-label="Name" defaultValue="health-monitor" disabled />
      <Select aria-label="Agent" disabled>
        <option>health-monitor</option>
      </Select>
      <Textarea aria-label="Instructions" defaultValue="Check service health." disabled />
      <CodeEditor aria-label="Agent definition" defaultValue="name: health-monitor" disabled />
    </div>
  ),
};

export const Invalid: StoryObj = {
  render: () => (
    <div className="form-col max-w-md">
      <label className="flex flex-col gap-1">
        Agent name
        <Input defaultValue="Health Monitor" aria-invalid="true" aria-describedby="name-error"
               className="border-danger" />
      </label>
      <p id="name-error" className="text-sm text-danger">Use lowercase letters, numbers, or hyphens.</p>
      <label className="flex flex-col gap-1">
        Schedule
        <Select defaultValue="" aria-invalid="true" aria-describedby="schedule-error"
                className="border-danger">
          <option value="">Choose a schedule</option>
          <option value="daily">Daily</option>
        </Select>
      </label>
      <p id="schedule-error" className="text-sm text-danger">Choose a schedule to continue.</p>
    </div>
  ),
};

export const ReadOnly: StoryObj = {
  render: () => (
    <div className="form-col max-w-md">
      <Input aria-label="Name" defaultValue="health-monitor" readOnly />
      <Textarea aria-label="Instructions" defaultValue="Check service health." readOnly />
      <CodeEditor aria-label="Agent definition" defaultValue={"name: health-monitor\ntools: []"} readOnly />
    </div>
  ),
};
